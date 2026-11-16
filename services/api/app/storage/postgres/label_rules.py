"""PostgreSQL repository for label rule versions.

`(review_id, version)` is the primary key, and storing is `INSERT … ON CONFLICT DO
NOTHING`: of two people applying rules at once, the second learns it lost.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.label_rules import LabelRules, LabelRulesVersion, UnclearCountsAs
from app.storage.postgres.tables import label_rule_versions


def _to_version(row: Row[Any]) -> LabelRulesVersion:
    return LabelRulesVersion(
        review_id=row.review_id,
        project_id=row.project_id,
        version=row.version,
        rules=LabelRules(
            possibly_min_met=row.possibly_min_met,
            unclear_counts_as=UnclearCountsAs(row.unclear_counts_as),
        ),
        created_by=row.created_by,
        created_at=row.created_at,
        papers_moved=row.papers_moved,
    )


class PostgresLabelRulesRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, version: LabelRulesVersion, /) -> bool:
        result = await self._connection.execute(
            insert(label_rule_versions)
            .values(
                review_id=version.review_id,
                version=version.version,
                project_id=version.project_id,
                possibly_min_met=version.rules.possibly_min_met,
                unclear_counts_as=version.rules.unclear_counts_as.value,
                created_by=version.created_by,
                created_at=version.created_at,
                papers_moved=version.papers_moved,
            )
            .on_conflict_do_nothing(index_elements=["review_id", "version"])
        )
        return result.rowcount == 1

    async def latest(self, review_id: UUID) -> LabelRulesVersion | None:
        row = (
            await self._connection.execute(
                select(label_rule_versions)
                .where(label_rule_versions.c.review_id == review_id)
                .order_by(label_rule_versions.c.version.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else _to_version(row)

    async def list_for_review(self, review_id: UUID) -> list[LabelRulesVersion]:
        rows = await self._connection.execute(
            select(label_rule_versions)
            .where(label_rule_versions.c.review_id == review_id)
            .order_by(label_rule_versions.c.version)
        )
        return [_to_version(row) for row in rows]
