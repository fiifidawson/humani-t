"""PostgreSQL repositories for papers, what runs discovered, kept texts and screening decisions.

Writes a re-run repeats are `INSERT … ON CONFLICT DO NOTHING` and report
whether they inserted: a second worker that raced the first, or a stage run
again after a crash, learns it lost instead of duplicating or overwriting.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.discovery import (
    PaperSet,
    PaperText,
    RunPaper,
    RunSource,
    SourceOutcome,
    StoredPaper,
    TextKind,
)
from app.domain.screening import Priority, ScreeningLabel, StoredDecision
from app.storage.postgres.tables import (
    paper_texts,
    papers,
    run_paper_sets,
    run_papers,
    run_sources,
    screening_decisions,
)


def _to_paper(row: Row[Any]) -> StoredPaper:
    return StoredPaper(
        id=row.id,
        project_id=row.project_id,
        review_id=row.review_id,
        first_run_id=row.first_run_id,
        identity_digest=row.identity_digest,
        source_id=row.source_id,
        record_sealed=row.record_sealed,
        created_at=row.created_at,
    )


def _to_text(row: Row[Any]) -> PaperText:
    return PaperText(
        paper_id=row.paper_id,
        project_id=row.project_id,
        kind=TextKind(row.kind),
        text_sealed=row.text_sealed,
        ingested_at=row.ingested_at,
    )


class PostgresPaperRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, paper_id: UUID) -> StoredPaper | None:
        row = (
            await self._connection.execute(select(papers).where(papers.c.id == paper_id))
        ).one_or_none()
        return None if row is None else _to_paper(row)

    async def find_by_identity(
        self, review_id: UUID, digests: Collection[bytes]
    ) -> list[StoredPaper]:
        if not digests:
            return []
        result = await self._connection.execute(
            select(papers).where(
                papers.c.review_id == review_id, papers.c.identity_digest.in_(list(digests))
            )
        )
        return [_to_paper(row) for row in result]

    async def add(self, paper: StoredPaper, /) -> None:
        await self._connection.execute(
            insert(papers).values(
                id=paper.id,
                project_id=paper.project_id,
                review_id=paper.review_id,
                first_run_id=paper.first_run_id,
                identity_digest=paper.identity_digest,
                source_id=paper.source_id,
                record_sealed=paper.record_sealed,
                created_at=paper.created_at,
            )
        )

    async def get_set(self, run_id: UUID) -> PaperSet | None:
        row = (
            await self._connection.execute(
                select(run_paper_sets).where(run_paper_sets.c.run_id == run_id)
            )
        ).one_or_none()
        return None if row is None else PaperSet(row.run_id, row.basis_run_id, row.recorded_at)

    async def add_set(self, paper_set: PaperSet, /) -> bool:
        result = await self._connection.execute(
            pg_insert(run_paper_sets)
            .values(
                run_id=paper_set.run_id,
                basis_run_id=paper_set.basis_run_id,
                recorded_at=paper_set.recorded_at,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        return result.rowcount == 1

    async def add_run_papers(self, papers_seen: Sequence[RunPaper], /) -> None:
        if not papers_seen:
            return
        await self._connection.execute(
            insert(run_papers),
            [
                {
                    "run_id": p.run_id,
                    "paper_id": p.paper_id,
                    "position": p.position,
                    "found_by": list(p.found_by),
                }
                for p in papers_seen
            ],
        )

    async def list_run_papers(self, run_id: UUID) -> list[RunPaper]:
        result = await self._connection.execute(
            select(run_papers)
            .where(run_papers.c.run_id == run_id)
            .order_by(run_papers.c.position)
        )
        return [
            RunPaper(row.run_id, row.paper_id, row.position, tuple(row.found_by))
            for row in result
        ]

    async def add_run_sources(self, sources: Sequence[RunSource], /) -> None:
        if not sources:
            return
        await self._connection.execute(
            insert(run_sources),
            [
                {
                    "run_id": s.run_id,
                    "source_id": s.source_id,
                    "outcome": s.outcome.value,
                    "found": s.found,
                    "plan_version": s.plan_version,
                    "vocabulary_version": s.vocabulary_version,
                    "query_sealed": s.query_sealed,
                    "searched_at": s.searched_at,
                }
                for s in sources
            ],
        )

    async def list_run_sources(self, run_id: UUID) -> list[RunSource]:
        result = await self._connection.execute(
            select(run_sources)
            .where(run_sources.c.run_id == run_id)
            .order_by(run_sources.c.source_id)
        )
        return [
            RunSource(
                run_id=row.run_id,
                source_id=row.source_id,
                outcome=SourceOutcome(row.outcome),
                found=row.found,
                plan_version=row.plan_version,
                vocabulary_version=row.vocabulary_version,
                query_sealed=row.query_sealed,
                searched_at=row.searched_at,
            )
            for row in result
        ]

    async def add_text(self, text: PaperText, /) -> bool:
        result = await self._connection.execute(
            pg_insert(paper_texts)
            .values(
                paper_id=text.paper_id,
                project_id=text.project_id,
                kind=text.kind.value,
                text_sealed=text.text_sealed,
                ingested_at=text.ingested_at,
            )
            .on_conflict_do_nothing(index_elements=["paper_id"])
        )
        return result.rowcount == 1

    async def list_texts(self, paper_ids: Collection[UUID]) -> list[PaperText]:
        if not paper_ids:
            return []
        result = await self._connection.execute(
            select(paper_texts).where(paper_texts.c.paper_id.in_(list(paper_ids)))
        )
        return [_to_text(row) for row in result]


def _to_decision(row: Row[Any]) -> StoredDecision:
    return StoredDecision(
        run_id=row.run_id,
        paper_id=row.paper_id,
        project_id=row.project_id,
        label=ScreeningLabel(row.label),
        priority=Priority(row.priority),
        score=row.score,
        criteria_matched=tuple(row.criteria_matched),
        exclusion_reasons=tuple(row.exclusion_reasons),
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        criteria_version=row.criteria_version,
        evidence_kind=TextKind(row.evidence_kind),
        is_unreadable=row.is_unreadable,
        decided_at=row.decided_at,
        rationale_sealed=row.rationale_sealed,
        answers_sealed=row.answers_sealed,
    )


class PostgresScreeningRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, decision: StoredDecision, /) -> bool:
        result = await self._connection.execute(
            pg_insert(screening_decisions)
            .values(
                run_id=decision.run_id,
                paper_id=decision.paper_id,
                project_id=decision.project_id,
                label=decision.label.value,
                priority=decision.priority.value,
                score=decision.score,
                criteria_matched=list(decision.criteria_matched),
                exclusion_reasons=list(decision.exclusion_reasons),
                model_id=decision.model_id,
                prompt_version=decision.prompt_version,
                criteria_version=decision.criteria_version,
                evidence_kind=decision.evidence_kind.value,
                is_unreadable=decision.is_unreadable,
                decided_at=decision.decided_at,
                rationale_sealed=decision.rationale_sealed,
                answers_sealed=decision.answers_sealed,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "paper_id"])
        )
        return result.rowcount == 1

    async def get(self, run_id: UUID, paper_id: UUID) -> StoredDecision | None:
        row = (
            await self._connection.execute(
                select(screening_decisions).where(
                    screening_decisions.c.run_id == run_id,
                    screening_decisions.c.paper_id == paper_id,
                )
            )
        ).one_or_none()
        return None if row is None else _to_decision(row)

    async def list_for_run(self, run_id: UUID) -> list[StoredDecision]:
        result = await self._connection.execute(
            select(screening_decisions)
            .where(screening_decisions.c.run_id == run_id)
            .order_by(screening_decisions.c.decided_at, screening_decisions.c.paper_id)
        )
        return [_to_decision(row) for row in result]
