"""The `discover`, `ingest` and `export` stages: find a run's papers, keep their text, count.

Each is a `Stage` (`app/runs/base.py`) and keeps its two rules. A stage may run
more than once, so each stores its work in one transaction guarded by a
conditional write, and a repeat finds that work and reports it instead of
redoing it. Everything reported is shown to every member, so progress items
and summaries carry counts, source ids and the plan's own query, never paper
text or a source's internal error.

`discover` compiles its query from the accepted plan (`PlanSource`) rather than
reading the `queries` stage's summary: the plan is typed and versioned, a run
may start without `queries`, and the version used is recorded with the result.
Screening is in `screening.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

import structlog

from app.application.papers import PaperStore, prisma_for
from app.domain.discovery import (
    FoundPaper,
    PaperSet,
    RunPaper,
    RunSource,
    SourceOutcome,
    StoredPaper,
    TextKind,
    identity_digest,
    merge_found,
    screening_text,
)
from app.domain.paper import PaperRecord
from app.domain.plans import PlanSource, ReviewPlan
from app.domain.query import compile_vocabulary
from app.domain.runs import JsonValue, Run
from app.domain.screening import PrismaCounts
from app.domain.sources import PaperSourceUnavailableError
from app.runs.base import StageContext, StageFailedError
from app.search.base import DEFAULT_LIMIT, PaperSearchProvider
from app.storage.base import StorageProvider, UnitOfWork

NO_PLAN = "This review has no accepted plan yet. Accept the search plan, then run again."
NO_SOURCES = (
    "No paper sources are switched on for this deployment. Ask your administrator to "
    "turn one on."
)
ALL_SOURCES_FAILED = (
    "None of the paper sources could be searched just now. Nothing was stored; try the "
    "run again later."
)
NOTHING_FOUND = "The search found no papers. Ask for changes to broaden it, then run it again."
NO_PAPERS = "This review has no papers yet. Run Discover first."

FULL_TEXT_GAP = (
    "Full text is not read yet: papers are masked and screened on their title and "
    "abstract only."
)
DOWNLOADS_GAP = "Downloadable files (dataset.csv, dataset.jsonl, prisma.md) come later."

_INGEST_BATCH = 50

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def accepted_plan_or_fail(
    storage: StorageProvider, plans: PlanSource[UnitOfWork], review_id: UUID
) -> ReviewPlan:
    """The review's accepted plan, or a plain `StageFailedError`."""
    async with storage.unit_of_work() as uow:
        plan = await plans.accepted_plan(uow, review_id)
    if plan is None:
        raise StageFailedError(NO_PLAN)
    return plan


async def held_run(uow: UnitOfWork, context: StageContext) -> Run:
    run = await uow.runs.get(context.run_id)
    if run is None:
        raise ValueError(f"run {context.run_id} does not exist")
    return run


async def adopted_or_fail(store: PaperStore, uow: UnitOfWork, run: Run) -> PaperSet:
    paper_set = await store.adopt(uow, run)
    if paper_set is None:
        raise StageFailedError(NO_PAPERS)
    return paper_set


def adoption_facts(paper_set: PaperSet) -> dict[str, JsonValue]:
    """What a stage adds to its manifest when it worked from another run's papers."""
    return {} if paper_set.is_own else {"papers_from_run": str(paper_set.basis_run_id)}


class DiscoverStage:
    """Search every enabled source with the plan's query, merge, and store the run's papers."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        store: PaperStore,
        plans: PlanSource[UnitOfWork],
        sources: Sequence[PaperSearchProvider],
        limit_per_source: int = DEFAULT_LIMIT,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._store = store
        self._plans = plans
        self._sources = tuple(sources)
        self._limit = limit_per_source
        self._clock = clock

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._storage.unit_of_work() as uow:
            run = await held_run(uow, context)
            if await uow.papers.get_set(run.id) is not None:
                return await self._summary(uow, run)
            plan = await self._plans.accepted_plan(uow, run.review_id)
        if plan is None:
            raise StageFailedError(NO_PLAN)
        if not self._sources:
            raise StageFailedError(NO_SOURCES)
        found, failed = await self._search(context, plan)
        if not found:
            raise StageFailedError(ALL_SOURCES_FAILED)
        # Stopping here names the real problem. Carrying on, every later stage works
        # on nothing and the brief reports it as a screening result.
        if not any(records for _, records in found):
            raise StageFailedError(NOTHING_FOUND)
        async with self._storage.unit_of_work() as uow:
            if await self._store_found(uow, run, plan, found, failed):
                await uow.commit()
        async with self._storage.unit_of_work() as uow:
            return await self._summary(uow, run)

    async def _search(
        self, context: StageContext, plan: ReviewPlan
    ) -> tuple[list[tuple[str, Sequence[PaperRecord]]], list[str]]:
        query = compile_vocabulary(plan.vocabulary)
        found: list[tuple[str, Sequence[PaperRecord]]] = []
        failed: list[str] = []
        total = len(self._sources)
        for done, source in enumerate(self._sources, start=1):
            await context.raise_if_cancelled()
            try:
                records = await source.search(query, limit=self._limit)
            except PaperSourceUnavailableError as exc:
                # The reason may name hosts and addresses: the operator's log only.
                _log.warning(
                    "paper_source_failed",
                    run_id=str(context.run_id),
                    source=source.source_id,
                    reason=exc.reason,
                )
                failed.append(source.source_id)
                await context.progress(
                    done, total, f"{source.source_id} · could not be searched"
                )
                continue
            found.append((source.source_id, tuple(records)))
            await context.progress(done, total, f"{source.source_id} · {len(records)} found")
        return found, failed

    async def _store_found(
        self,
        uow: UnitOfWork,
        run: Run,
        plan: ReviewPlan,
        found: Sequence[tuple[str, Sequence[PaperRecord]]],
        failed: Sequence[str],
    ) -> bool:
        """Store the merged papers once. False if another execution stored them first."""
        now = self._clock()
        if not await uow.papers.add_set(PaperSet(run.id, run.id, now)):
            return False
        merged = merge_found(found)
        key = await self._store.identity_key(uow, run.project_id)
        digests = {m.record.identity: identity_digest(key, m.record.identity) for m in merged}
        known = {
            p.identity_digest: p
            for p in await uow.papers.find_by_identity(run.review_id, list(digests.values()))
        }
        entries: list[RunPaper] = []
        for position, paper in enumerate(merged, start=1):
            digest = digests[paper.record.identity]
            stored = known.get(digest) or await self._add_paper(uow, run, paper, digest, now)
            entries.append(RunPaper(run.id, stored.id, position, paper.found_by))
        await uow.papers.add_run_papers(entries)
        query = compile_vocabulary(plan.vocabulary).render()
        counts = {source_id: len(records) for source_id, records in found}
        await uow.papers.add_run_sources(
            [
                RunSource(
                    run_id=run.id,
                    source_id=source.source_id,
                    outcome=(
                        SourceOutcome.FAILED
                        if source.source_id in failed
                        else SourceOutcome.SEARCHED
                    ),
                    found=counts.get(source.source_id, 0),
                    plan_version=plan.version,
                    vocabulary_version=plan.vocabulary.version,
                    query_sealed=await self._store.seal_query(
                        uow, run.project_id, run.id, source.source_id, query
                    ),
                    searched_at=now,
                )
                for source in self._sources
            ]
        )
        return True

    async def _add_paper(
        self, uow: UnitOfWork, run: Run, paper: FoundPaper, digest: bytes, now: datetime
    ) -> StoredPaper:
        paper_id = uuid.uuid4()
        stored = StoredPaper(
            id=paper_id,
            project_id=run.project_id,
            review_id=run.review_id,
            first_run_id=run.id,
            identity_digest=digest,
            source_id=paper.record.source_id,
            record_sealed=await self._store.seal_record(
                uow, run.project_id, paper_id, paper.record
            ),
            created_at=now,
        )
        await uow.papers.add(stored)
        return stored

    async def _summary(self, uow: UnitOfWork, run: Run) -> dict[str, JsonValue]:
        """Rebuilt from what was stored, so a repeat reports exactly what the first stored."""
        sources = sorted(await uow.papers.list_run_sources(run.id), key=lambda s: s.source_id)
        papers = await uow.papers.list_run_papers(run.id)
        searched = {
            s.source_id: s.found for s in sources if s.outcome is SourceOutcome.SEARCHED
        }
        identified = sum(searched.values())
        new = 0
        for paper in papers:
            stored = await uow.papers.get(paper.paper_id)
            if stored is not None and stored.first_run_id == run.id:
                new += 1
        first = sources[0] if sources else None
        return {
            "identified": identified,
            "by_source": dict(searched),
            "failed_sources": [
                s.source_id for s in sources if s.outcome is SourceOutcome.FAILED
            ],
            "after_deduplication": len(papers),
            "duplicates_removed": identified - len(papers),
            "new_papers": new,
            "manifest": {
                "plan_version": first.plan_version if first else None,
                "vocabulary_version": first.vocabulary_version if first else None,
                "sources": [s.source_id for s in sources],
                "queries": {
                    s.source_id: await self._store.open_query(uow, run.project_id, s)
                    for s in sources
                },
                "limit_per_source": self._limit,
            },
        }


class IngestStage:
    """Keep each paper's text for the mask stage: title and abstract (P1: no full text)."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        store: PaperStore,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._store = store
        self._clock = clock

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._storage.unit_of_work() as uow:
            run = await held_run(uow, context)
            paper_set = await adopted_or_fail(self._store, uow, run)
            await uow.commit()
            papers = await self._store.run_papers(uow, run)
        total = len(papers)
        kept = 0
        for start in range(0, total, _INGEST_BATCH):
            await context.raise_if_cancelled()
            batch = papers[start : start + _INGEST_BATCH]
            async with self._storage.unit_of_work() as uow:
                for paper in batch:
                    record = await self._store.open_record(uow, run.project_id, paper.paper_id)
                    kind, text = screening_text(record)
                    sealed = await self._store.seal_text(
                        uow, run.project_id, paper.paper_id, kind, text
                    )
                    kept += await uow.papers.add_text(sealed)
                await uow.commit()
            await context.progress(
                start + len(batch), total, f"{start + len(batch)} papers read"
            )
        async with self._storage.unit_of_work() as uow:
            return await self._summary(uow, run, paper_set, papers, kept)

    async def _summary(
        self,
        uow: UnitOfWork,
        run: Run,
        paper_set: PaperSet,
        papers: Sequence[RunPaper],
        kept: int,
    ) -> dict[str, JsonValue]:
        texts = await uow.papers.list_texts([p.paper_id for p in papers])
        kinds = [t.kind for t in texts]
        open_access = 0
        for paper in papers:
            record = await self._store.open_record(uow, run.project_id, paper.paper_id)
            open_access += record.is_open_access
        return {
            "papers": len(papers),
            "abstracts": kinds.count(TextKind.ABSTRACT),
            "title_only": kinds.count(TextKind.TITLE),
            "full_texts": 0,
            "open_access_not_read": open_access,
            "kept": kept,
            "already_kept": len(texts) - kept,
            "gaps": [FULL_TEXT_GAP],
            "manifest": {
                "text_kinds": [kind.value for kind in sorted(set(kinds))],
                **adoption_facts(paper_set),
            },
        }


def prisma_summary(counts: PrismaCounts) -> dict[str, JsonValue]:
    return {
        "identified": counts.identified,
        "identified_by_source": dict(counts.identified_by_source),
        "failed_sources": list(counts.failed_sources),
        "duplicates_removed": counts.duplicates_removed,
        "after_deduplication": counts.after_deduplication,
        "screened": counts.screened,
        "included": counts.included,
        "excluded": counts.excluded,
        "uncertain": counts.uncertain,
        "not_screened": counts.not_screened,
        "excluded_by_reason": dict(counts.excluded_by_reason),
    }


class ExportStage:
    """P1: the PRISMA counts and a dataset summary, in the stage summary. No files yet."""

    def __init__(self, *, storage: StorageProvider, store: PaperStore) -> None:
        self._storage = storage
        self._store = store

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._storage.unit_of_work() as uow:
            run = await held_run(uow, context)
            paper_set = await adopted_or_fail(self._store, uow, run)
            await uow.commit()
            papers = await self._store.run_papers(uow, run)
            decisions = await uow.screening.list_for_run(run.id)
            counts = prisma_for(await self._store.run_sources(uow, run), len(papers), decisions)
            dataset = await self._dataset(uow, run, papers)
        await context.progress(1, 1, f"{len(papers)} papers counted")
        return {
            "prisma": prisma_summary(counts),
            "dataset": dataset,
            "gaps": [DOWNLOADS_GAP],
            "manifest": adoption_facts(paper_set),
        }

    async def _dataset(
        self, uow: UnitOfWork, run: Run, papers: Sequence[RunPaper]
    ) -> dict[str, JsonValue]:
        by_source: dict[str, int] = {}
        years: list[int] = []
        open_access = with_abstract = 0
        for paper in papers:
            stored = await uow.papers.get(paper.paper_id)
            if stored is None:
                continue
            record = await self._store.open_paper(uow, stored)
            by_source[stored.source_id] = by_source.get(stored.source_id, 0) + 1
            if record.year is not None:
                years.append(record.year)
            open_access += record.is_open_access
            with_abstract += bool((record.abstract or "").strip())
        return {
            "papers": len(papers),
            "by_source": dict(sorted(by_source.items())),
            "years": {"earliest": min(years), "latest": max(years)} if years else None,
            "open_access": open_access,
            "with_abstract": with_abstract,
        }
