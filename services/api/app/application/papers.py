"""Papers, kept texts and decisions as the use cases see them: sealed in storage, clear here.

`PaperStore` is the one place that knows how each is sealed and bound to its row.
Everything leaving `PaperService` is masked text or bibliographic metadata; raw kept text
is read only by the mask stage, through `texts_for_run`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.accounts import Current
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.run_log import RunLog
from app.domain.accounts import NotFoundError
from app.domain.discovery import (
    IngestedText,
    PaperSet,
    PaperText,
    RunPaper,
    RunSource,
    SourceOutcome,
    StoredPaper,
    TextKind,
)
from app.domain.documents import DocumentPassageSource
from app.domain.encryption import EncryptionContext
from app.domain.manifest import RunManifest, StageSummary, assemble_manifest
from app.domain.paper import PaperRecord
from app.domain.projects import Action
from app.domain.runs import EventName, Run
from app.domain.screening import (
    CriterionAnswer,
    CriterionVerdict,
    MaskedTextSource,
    Passage,
    PassageSourceKind,
    PrismaCounts,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
    StoredDecision,
    passage_tag,
    prisma_counts,
)
from app.storage.base import StorageProvider, UnitOfWork

_IDENTITY_KEY_PURPOSE = "papers.identity"
_RECORD_PURPOSE = "papers.record"
_QUERY_PURPOSE = "run_sources.query"
_TEXT_PURPOSE = "paper_texts.text"
_RATIONALE_PURPOSE = "screening_decisions.rationale"
_ANSWERS_PURPOSE = "screening_decisions.answers"

_REVIEW_NOT_FOUND = "No such review."
_RUN_NOT_FOUND = "No such run."
_PAPER_NOT_FOUND = "No such paper."
_EVENTS_BATCH = 10_000

PAGE_SIZE = 50


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _record_json(record: PaperRecord) -> bytes:
    return json.dumps(
        {
            "source_id": record.source_id,
            "external_id": record.external_id,
            "title": record.title,
            "year": record.year,
            "doi": record.doi,
            "is_open_access": record.is_open_access,
            "abstract": record.abstract,
            "url": record.url,
        },
        sort_keys=True,
    ).encode()


def _record_from_json(data: bytes) -> PaperRecord:
    values: dict[str, Any] = json.loads(data)
    return PaperRecord(**values)


class PaperStore:
    """Seals and opens the pipeline's rows inside the caller's unit of work."""

    def __init__(
        self, encryption: ScopeEncryption, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._encryption = encryption
        self._clock = clock

    async def identity_key(self, uow: UnitOfWork, project_id: UUID) -> bytes:
        return await self._encryption.derive_key(uow, project_id, _IDENTITY_KEY_PURPOSE)

    async def seal_record(
        self, uow: UnitOfWork, project_id: UUID, paper_id: UUID, record: PaperRecord
    ) -> bytes:
        context = EncryptionContext(project_id, _RECORD_PURPOSE, str(paper_id))
        return await self._encryption.encrypt(uow, context, _record_json(record))

    async def open_paper(self, uow: UnitOfWork, paper: StoredPaper) -> PaperRecord:
        context = EncryptionContext(paper.project_id, _RECORD_PURPOSE, str(paper.id))
        return _record_from_json(
            await self._encryption.decrypt(uow, context, paper.record_sealed)
        )

    async def open_record(
        self, uow: UnitOfWork, project_id: UUID, paper_id: UUID
    ) -> PaperRecord:
        """Raises `KeyDestroyedError` once the project is deleted."""
        paper = await uow.papers.get(paper_id)
        if paper is None or paper.project_id != project_id:
            raise NotFoundError(_PAPER_NOT_FOUND)
        return await self.open_paper(uow, paper)

    async def seal_query(
        self, uow: UnitOfWork, project_id: UUID, run_id: UUID, source_id: str, query: str
    ) -> bytes:
        context = EncryptionContext(project_id, _QUERY_PURPOSE, f"{run_id}:{source_id}")
        return await self._encryption.encrypt(uow, context, query.encode())

    async def open_query(self, uow: UnitOfWork, project_id: UUID, source: RunSource) -> str:
        context = EncryptionContext(
            project_id, _QUERY_PURPOSE, f"{source.run_id}:{source.source_id}"
        )
        return (await self._encryption.decrypt(uow, context, source.query_sealed)).decode()

    async def seal_text(
        self, uow: UnitOfWork, project_id: UUID, paper_id: UUID, kind: TextKind, text: str
    ) -> PaperText:
        context = EncryptionContext(project_id, _TEXT_PURPOSE, str(paper_id))
        sealed = await self._encryption.encrypt(uow, context, text.encode())
        return PaperText(paper_id, project_id, kind, sealed, self._clock())

    async def open_text(self, uow: UnitOfWork, text: PaperText) -> str:
        context = EncryptionContext(text.project_id, _TEXT_PURPOSE, str(text.paper_id))
        return (await self._encryption.decrypt(uow, context, text.text_sealed)).decode()

    async def seal_decision(
        self, uow: UnitOfWork, project_id: UUID, decision: ScreeningDecision
    ) -> StoredDecision:
        sealed = await self._encryption.encrypt(
            uow,
            _rationale_context(project_id, decision.run_id, decision.paper_id),
            decision.rationale.encode(),
        )
        answers = None
        if decision.criterion_answers:
            answers = await self._encryption.encrypt(
                uow,
                _answers_context(project_id, decision.run_id, decision.paper_id),
                _answers_json(decision.criterion_answers),
            )
        return StoredDecision.sealed(decision, project_id, sealed, answers)

    async def open_decision(self, uow: UnitOfWork, stored: StoredDecision) -> ScreeningDecision:
        rationale = await self._encryption.decrypt(
            uow,
            _rationale_context(stored.project_id, stored.run_id, stored.paper_id),
            stored.rationale_sealed,
        )
        answers: tuple[CriterionAnswer, ...] = ()
        if stored.answers_sealed is not None:
            answers = _answers_from_json(
                await self._encryption.decrypt(
                    uow,
                    _answers_context(stored.project_id, stored.run_id, stored.paper_id),
                    stored.answers_sealed,
                )
            )
        return stored.opened(rationale.decode(), answers)

    async def adopt(self, uow: UnitOfWork, run: Run) -> PaperSet | None:
        """The run's paper set; a run started without `discover` adopts its review's newest.

        `None` when there is nothing to work from: the run should have discovered
        and has not, or the review has never discovered anything.
        """
        own = await uow.papers.get_set(run.id)
        if own is not None or "discover" in run.stages:
            return own
        for earlier in await uow.runs.list_for_review(run.review_id):
            if earlier.id == run.id or earlier.created_at > run.created_at:
                continue
            found = await uow.papers.get_set(earlier.id)
            if found is not None:
                adopted = PaperSet(run.id, found.basis_run_id, self._clock())
                if not await uow.papers.add_set(adopted):
                    return await uow.papers.get_set(run.id)
                return adopted
        return None

    async def screened_by(self, uow: UnitOfWork, run: Run) -> Run:
        """The run whose screening decisions apply to `run`'s papers.

        If `run` did not screen (a retry from Brief, say), the newest earlier run that
        screened the same paper set, else `run` itself, with no decisions.
        """
        if "screen" in run.stages:
            return run
        own = await uow.papers.get_set(run.id)
        if own is None:
            return run
        for earlier in await uow.runs.list_for_review(run.review_id):
            # As in `adopt`: only later runs are skipped, so equal times still count.
            if earlier.id == run.id or earlier.created_at > run.created_at:
                continue
            if "screen" not in earlier.stages:
                continue
            found = await uow.papers.get_set(earlier.id)
            if (
                found is not None
                and found.basis_run_id == own.basis_run_id
                and await uow.screening.list_for_run(earlier.id)
            ):
                return earlier
        return run

    async def run_papers(self, uow: UnitOfWork, run: Run) -> list[RunPaper]:
        """The papers the run works from, by position; empty before it has any."""
        paper_set = await uow.papers.get_set(run.id)
        if paper_set is None:
            return []
        return await uow.papers.list_run_papers(paper_set.basis_run_id)

    async def run_sources(self, uow: UnitOfWork, run: Run) -> list[RunSource]:
        paper_set = await uow.papers.get_set(run.id)
        if paper_set is None:
            return []
        return await uow.papers.list_run_sources(paper_set.basis_run_id)

    async def texts_for_run(
        self, uow: UnitOfWork, project_id: UUID, run_id: UUID
    ) -> tuple[IngestedText, ...]:
        """`IngestedTextSource`: the raw kept texts of the run's papers, in the run's order.

        For the mask stage only. Nothing read here may reach a model or a member.
        """
        run = await uow.runs.get(run_id)
        if run is None or run.project_id != project_id:
            return ()
        papers = await self.run_papers(uow, run)
        texts = {
            t.paper_id: t for t in await uow.papers.list_texts([p.paper_id for p in papers])
        }
        return tuple(
            [
                IngestedText(
                    p.paper_id,
                    texts[p.paper_id].kind,
                    await self.open_text(uow, texts[p.paper_id]),
                )
                for p in papers
                if p.paper_id in texts
            ]
        )


def _rationale_context(project_id: UUID, run_id: UUID, paper_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _RATIONALE_PURPOSE, f"{run_id}:{paper_id}")


def _answers_context(project_id: UUID, run_id: UUID, paper_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _ANSWERS_PURPOSE, f"{run_id}:{paper_id}")


def _answers_json(answers: Sequence[CriterionAnswer]) -> bytes:
    return json.dumps(
        [{"id": a.criterion_id, "answer": a.verdict.value, "quote": a.quote} for a in answers]
    ).encode()


def _answers_from_json(data: bytes) -> tuple[CriterionAnswer, ...]:
    values: list[dict[str, Any]] = json.loads(data)
    return tuple(
        CriterionAnswer(v["id"], CriterionVerdict(v["answer"]), v["quote"]) for v in values
    )


class ScreenedEvidence:
    """`EvidenceSource`: the masked passages of a run's included papers, tagged by place.

    With `documents`, approved documents follow, tagged `D<n>`: the team chose them, so
    they are evidence without screening once an owner approved their masking.
    """

    def __init__(
        self,
        store: PaperStore,
        masked: MaskedTextSource[UnitOfWork],
        documents: DocumentPassageSource[UnitOfWork] | None = None,
    ) -> None:
        self._store = store
        self._masked = masked
        self._documents = documents

    async def included_passages(
        self, uow: UnitOfWork, review_id: UUID, run_id: UUID
    ) -> tuple[Passage, ...]:
        run = await uow.runs.get(run_id)
        if run is None or run.review_id != review_id:
            return ()
        decided_by = await self._store.screened_by(uow, run)
        included = {
            d.paper_id
            for d in await uow.screening.list_for_run(decided_by.id)
            if d.label is ScreeningLabel.INCLUDE
        }
        passages: list[Passage] = []
        for paper in await self._store.run_papers(uow, run):
            if paper.paper_id not in included:
                continue
            text = await self._masked.masked_text(uow, run.project_id, paper.paper_id)
            if text is None or not text.strip():
                continue
            passages.append(
                Passage(
                    tag=passage_tag(paper.position),
                    paper_id=paper.paper_id,
                    source_kind=PassageSourceKind.PAPER,
                    text=text,
                    locator=TextKind.ABSTRACT.value,
                )
            )
        if self._documents is not None:
            passages.extend(await self._documents.approved_passages(uow, run.project_id))
        return tuple(passages)


SCREENED = "screened"
SKIPPED = "skipped"
FAILED = "failed"
NOT_SCREENED = "not_screened"


@dataclass(frozen=True, slots=True)
class PaperRow:
    """One row of the screening table: metadata, its place, and its decision if any.

    `status` is `skipped` when there was no masked text to send (see `skipped_because`)
    and `failed` when the privacy check stopped it.
    """

    paper_id: UUID
    tag: str
    position: int
    found_by: tuple[str, ...]
    record: PaperRecord
    decision: ScreeningDecision | None
    status: str = SCREENED
    skipped_because: str | None = None


@dataclass(frozen=True, slots=True)
class PaperPage:
    run_id: UUID
    page: int
    page_size: int
    total: int
    counts: dict[str, int]
    rows: tuple[PaperRow, ...]


@dataclass(frozen=True, slots=True)
class PaperDetail:
    row: PaperRow
    run_id: UUID
    text_kind: TextKind | None
    masked_text: str | None


@dataclass(frozen=True, slots=True)
class PrismaView:
    run_id: UUID
    counts: PrismaCounts


UNSCREENED = "unscreened"
LABEL_FILTERS = (*(label.value for label in ScreeningLabel), UNSCREENED)
TIER_FILTERS = (*(tier.value for tier in ScreeningTier), SKIPPED, FAILED, NOT_SCREENED)

# Most relevant first, as the screening table lists them.
_TIER_ORDER = {
    ScreeningTier.HIGHLY_RELEVANT: 0,
    ScreeningTier.RELEVANT: 1,
    ScreeningTier.POSSIBLY_RELEVANT: 2,
    ScreeningTier.UNCERTAIN: 3,
    ScreeningTier.EXCLUDED: 4,
}


def _row_order(row: tuple[RunPaper, StoredDecision | None]) -> tuple[int, int]:
    paper, decision = row
    if decision is None:
        return (len(_TIER_ORDER), paper.position)
    return (_TIER_ORDER[decision.tier], paper.position)


def tier_counts(
    decisions: Sequence[StoredDecision], papers: int, statuses: Sequence[str] = ()
) -> dict[str, int]:
    """Papers per tier, and those without a decision, in all and by why: the filter chips."""
    counts = {tier.value: 0 for tier in ScreeningTier}
    for decision in decisions:
        counts[decision.tier.value] += 1
    counts[UNSCREENED] = papers - len(decisions)
    for status in (SKIPPED, FAILED, NOT_SCREENED):
        counts[status] = sum(s == status for s in statuses)
    return counts


def prisma_for(
    sources: Sequence[RunSource], papers: int, decisions: Sequence[StoredDecision]
) -> PrismaCounts:
    return prisma_counts(
        identified_by_source={
            s.source_id: s.found for s in sources if s.outcome is SourceOutcome.SEARCHED
        },
        failed_sources=sorted(
            s.source_id for s in sources if s.outcome is SourceOutcome.FAILED
        ),
        after_deduplication=papers,
        decisions=decisions,
    )


class PaperService:
    """The paper, PRISMA and manifest routes. Each call is one read-only transaction."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        store: PaperStore,
        masked: MaskedTextSource[UnitOfWork],
        encryption: ScopeEncryption,
    ) -> None:
        self._storage = storage
        self._store = store
        self._masked = masked
        self._log = RunLog(encryption)

    async def authorize_review(self, current: Current, review_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._review_project(uow, current, review_id)

    async def list_papers(
        self,
        current: Current,
        review_id: UUID,
        *,
        run_id: UUID | None = None,
        label: str | None = None,
        page: int = 1,
        tier: str | None = None,
    ) -> PaperPage:
        """One page of a run's papers, most relevant first; the review's newest run by default.

        Raises `ValueError` for an unknown label or tier filter or a page below 1.
        """
        if label is not None and label not in LABEL_FILTERS:
            raise ValueError(f"label must be one of {', '.join(LABEL_FILTERS)}")
        if tier is not None and tier not in TIER_FILTERS:
            raise ValueError(f"tier must be one of {', '.join(TIER_FILTERS)}")
        if page < 1:
            raise ValueError("page counts from 1")
        async with self._storage.unit_of_work() as uow:
            await self._review_project(uow, current, review_id)
            run = await self._run_of(uow, review_id, run_id)
            papers = await self._store.run_papers(uow, run)
            decided = {d.paper_id: d for d in await uow.screening.list_for_run(run.id)}
            why = await self._undecided(
                uow, run, [p for p in papers if p.paper_id not in decided]
            )
            rows = sorted(((p, decided.get(p.paper_id)) for p in papers), key=_row_order)
            if label == UNSCREENED:
                rows = [r for r in rows if r[1] is None]
            elif label is not None:
                rows = [r for r in rows if r[1] is not None and r[1].label.value == label]
            if tier is not None:
                rows = [r for r in rows if _status_or_tier(r, why) == tier]
            start = (page - 1) * PAGE_SIZE
            chosen = rows[start : start + PAGE_SIZE]
            return PaperPage(
                run_id=run.id,
                page=page,
                page_size=PAGE_SIZE,
                total=len(rows),
                counts=tier_counts(
                    list(decided.values()), len(papers), [s for s, _ in why.values()]
                ),
                rows=tuple([await self._row(uow, run, p, d, why) for p, d in chosen]),
            )

    async def paper(
        self, current: Current, review_id: UUID, paper_id: UUID, *, run_id: UUID | None = None
    ) -> PaperDetail:
        async with self._storage.unit_of_work() as uow:
            project_id = await self._review_project(uow, current, review_id)
            run = await self._run_of(uow, review_id, run_id)
            seen = next(
                (p for p in await self._store.run_papers(uow, run) if p.paper_id == paper_id),
                None,
            )
            if seen is None:
                raise NotFoundError(_PAPER_NOT_FOUND)
            decision = await uow.screening.get(run.id, paper_id)
            why = {} if decision else await self._undecided(uow, run, [seen])
            row = await self._row(uow, run, seen, decision, why)
            texts = await uow.papers.list_texts([paper_id])
            return PaperDetail(
                row=row,
                run_id=run.id,
                text_kind=texts[0].kind if texts else None,
                masked_text=await self._masked.masked_text(uow, project_id, paper_id),
            )

    async def prisma(
        self, current: Current, review_id: UUID, *, run_id: UUID | None = None
    ) -> PrismaView:
        async with self._storage.unit_of_work() as uow:
            await self._review_project(uow, current, review_id)
            run = await self._run_of(uow, review_id, run_id)
            return PrismaView(
                run.id,
                prisma_for(
                    await self._store.run_sources(uow, run),
                    len(await self._store.run_papers(uow, run)),
                    await uow.screening.list_for_run(run.id),
                ),
            )

    async def manifest(self, current: Current, run_id: UUID) -> RunManifest:
        """The run's manifest from its `stage.finished` events, as stored."""
        async with self._storage.unit_of_work() as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFoundError(_RUN_NOT_FOUND)
            await require_member(
                uow,
                current.user.id,
                run.project_id,
                Action.READ_PROJECT,
                not_found=_RUN_NOT_FOUND,
            )
            events = await self._log.read(uow, run, 0, _EVENTS_BATCH)
        finished: list[StageSummary] = []
        for event in events:
            if event.name != EventName.STAGE_FINISHED:
                continue
            data = event.data
            stage, seconds, summary = (
                data.get("stage"),
                data.get("seconds"),
                data.get("summary"),
            )
            if (
                isinstance(stage, str)
                and isinstance(seconds, int | float)
                and isinstance(summary, dict)
            ):
                finished.append(StageSummary(stage, float(seconds), summary))
        return assemble_manifest(run, finished)

    async def _review_project(self, uow: UnitOfWork, current: Current, review_id: UUID) -> UUID:
        review = await uow.reviews.get(review_id)
        if review is None:
            raise NotFoundError(_REVIEW_NOT_FOUND)
        await require_member(
            uow,
            current.user.id,
            review.project_id,
            Action.READ_PROJECT,
            not_found=_REVIEW_NOT_FOUND,
        )
        return review.project_id

    async def _run_of(self, uow: UnitOfWork, review_id: UUID, run_id: UUID | None) -> Run:
        """The named run of this review, or its newest run that has papers."""
        if run_id is not None:
            run = await uow.runs.get(run_id)
            if run is None or run.review_id != review_id:
                raise NotFoundError(_RUN_NOT_FOUND)
            return run
        for run in await uow.runs.list_for_review(review_id):
            if await uow.papers.get_set(run.id) is not None:
                return run
        raise NotFoundError("This review has no papers yet.")

    async def _undecided(
        self, uow: UnitOfWork, run: Run, papers: Sequence[RunPaper]
    ) -> dict[UUID, tuple[str, str | None]]:
        """Why each paper has no decision in this run, and for a skipped one, what was missing.

        Failed papers come from the screen stage's rows in the run's log; skipped ones are
        those it passed over once it has finished.
        """
        if not papers:
            return {}
        events = await self._log.read(uow, run, 0, _EVENTS_BATCH)
        failed = {
            str(screened.get("paper_id"))
            for event in events
            if event.name == EventName.STAGE_PROGRESS
            and isinstance(screened := event.data.get("screened"), dict)
            and screened.get("outcome") == FAILED
        }
        has_screened = any(
            e.name == EventName.STAGE_FINISHED and e.data.get("stage") == "screen"
            for e in events
        )
        kinds = {
            t.paper_id: t.kind
            for t in await uow.papers.list_texts([p.paper_id for p in papers])
        }
        why: dict[UUID, tuple[str, str | None]] = {}
        for paper in papers:
            if str(paper.paper_id) in failed:
                why[paper.paper_id] = (FAILED, None)
            elif not has_screened:
                why[paper.paper_id] = (NOT_SCREENED, None)
            elif kinds.get(paper.paper_id) is not TextKind.ABSTRACT:
                why[paper.paper_id] = (SKIPPED, "no_abstract")
            elif not await self._masked.masked_text(uow, run.project_id, paper.paper_id):
                why[paper.paper_id] = (SKIPPED, "not_masked")
            else:
                why[paper.paper_id] = (NOT_SCREENED, None)
        return why

    async def _row(
        self,
        uow: UnitOfWork,
        run: Run,
        paper: RunPaper,
        decision: StoredDecision | None,
        why: dict[UUID, tuple[str, str | None]],
    ) -> PaperRow:
        status, skipped_because = why.get(paper.paper_id, (SCREENED, None))
        return PaperRow(
            paper_id=paper.paper_id,
            tag=passage_tag(paper.position),
            position=paper.position,
            found_by=paper.found_by,
            record=await self._store.open_record(uow, run.project_id, paper.paper_id),
            decision=None
            if decision is None
            else await self._store.open_decision(uow, decision),
            status=SCREENED if decision is not None else status,
            skipped_because=None if decision is not None else skipped_because,
        )


def _status_or_tier(
    row: tuple[RunPaper, StoredDecision | None], why: dict[UUID, tuple[str, str | None]]
) -> str:
    paper, decision = row
    return decision.tier.value if decision is not None else why[paper.paper_id][0]
