"""PostgreSQL repository for decision briefs.

One brief per run is a unique constraint, and storing is `INSERT … ON CONFLICT
DO NOTHING`: of two workers storing a brief for the same run, the second
learns it lost without an error and without a second row.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.briefs import BriefRecord, StopReason
from app.storage.postgres.tables import decision_briefs


def _to_brief(row: Row[Any]) -> BriefRecord:
    return BriefRecord(
        id=row.id,
        project_id=row.project_id,
        review_id=row.review_id,
        run_id=row.run_id,
        version=row.version,
        created_at=row.created_at,
        model_id=row.model_id,
        rounds=row.rounds,
        kept_round=row.kept_round,
        max_rounds=row.max_rounds,
        stopped_because=StopReason(row.stopped_because),
        content_sealed=row.content_sealed,
        evidence_sealed=row.evidence_sealed,
        trace_sealed=row.trace_sealed,
    )


class PostgresDecisionBriefRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, brief: BriefRecord, /) -> bool:
        result = await self._connection.execute(
            insert(decision_briefs)
            .values(
                id=brief.id,
                project_id=brief.project_id,
                review_id=brief.review_id,
                run_id=brief.run_id,
                version=brief.version,
                created_at=brief.created_at,
                model_id=brief.model_id,
                rounds=brief.rounds,
                kept_round=brief.kept_round,
                max_rounds=brief.max_rounds,
                stopped_because=brief.stopped_because.value,
                content_sealed=brief.content_sealed,
                evidence_sealed=brief.evidence_sealed,
                trace_sealed=brief.trace_sealed,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        return result.rowcount == 1

    async def _one(self, *conditions: Any) -> BriefRecord | None:
        row = (
            await self._connection.execute(select(decision_briefs).where(*conditions))
        ).one_or_none()
        return None if row is None else _to_brief(row)

    async def get(self, brief_id: UUID) -> BriefRecord | None:
        return await self._one(decision_briefs.c.id == brief_id)

    async def for_run(self, run_id: UUID) -> BriefRecord | None:
        return await self._one(decision_briefs.c.run_id == run_id)

    async def latest_for_review(self, review_id: UUID) -> BriefRecord | None:
        row = (
            await self._connection.execute(
                select(decision_briefs)
                .where(decision_briefs.c.review_id == review_id)
                .order_by(decision_briefs.c.version.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else _to_brief(row)

    async def count_for_review(self, review_id: UUID) -> int:
        count = await self._connection.scalar(
            select(func.count())
            .select_from(decision_briefs)
            .where(decision_briefs.c.review_id == review_id)
        )
        return int(count or 0)
