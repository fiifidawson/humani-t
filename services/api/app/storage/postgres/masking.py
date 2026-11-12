"""PostgreSQL repositories for project masking rules and papers' maskings.

A masking is replaced only by a conditional `UPDATE ... WHERE masked_with = :old`,
so two workers re-masking the same paper cannot both win, and a stale writer
learns it lost instead of overwriting newer work.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.masking import EntityType, MaskingRuleRecord, PaperMask
from app.storage.postgres.tables import masking_rules, paper_masks


def _to_rule(row: Row[Any]) -> MaskingRuleRecord:
    return MaskingRuleRecord(
        id=row.id,
        project_id=row.project_id,
        term_sealed=row.term_sealed,
        entity_type=EntityType(row.entity_type),
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _to_mask(row: Row[Any]) -> PaperMask:
    return PaperMask(
        paper_id=row.paper_id,
        project_id=row.project_id,
        masked_sealed=row.masked_sealed,
        spans_sealed=row.spans_sealed,
        masked_with=tuple(row.masked_with),
        masked_at=row.masked_at,
    )


class PostgresMaskingRuleRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, rule_id: UUID) -> MaskingRuleRecord | None:
        query = select(masking_rules).where(masking_rules.c.id == rule_id)
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else _to_rule(row)

    async def add(self, rule: MaskingRuleRecord, /) -> None:
        await self._connection.execute(
            insert(masking_rules).values(
                id=rule.id,
                project_id=rule.project_id,
                term_sealed=rule.term_sealed,
                entity_type=rule.entity_type.value,
                created_by=rule.created_by,
                created_at=rule.created_at,
            )
        )

    async def list_for_project(self, project_id: UUID) -> list[MaskingRuleRecord]:
        query = (
            select(masking_rules)
            .where(masking_rules.c.project_id == project_id)
            .order_by(masking_rules.c.created_at, masking_rules.c.id)
        )
        return [_to_rule(row) for row in await self._connection.execute(query)]

    async def delete(self, rule_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(masking_rules).where(masking_rules.c.id == rule_id)
        )
        return result.rowcount > 0


class PostgresPaperMaskRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, paper_id: UUID) -> PaperMask | None:
        query = select(paper_masks).where(paper_masks.c.paper_id == paper_id)
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else _to_mask(row)

    async def put(self, mask: PaperMask, /, *, replaces: tuple[str, ...] | None) -> bool:
        values: dict[str, Any] = {
            "project_id": mask.project_id,
            "masked_sealed": mask.masked_sealed,
            "spans_sealed": mask.spans_sealed,
            "masked_with": list(mask.masked_with),
            "masked_at": mask.masked_at,
        }
        if replaces is None:
            statement: Any = (
                pg_insert(paper_masks)
                .values(paper_id=mask.paper_id, **values)
                .on_conflict_do_nothing(index_elements=[paper_masks.c.paper_id])
            )
        else:
            statement = (
                update(paper_masks)
                .where(
                    paper_masks.c.paper_id == mask.paper_id,
                    paper_masks.c.masked_with == list(replaces),
                )
                .values(**values)
            )
        result = await self._connection.execute(statement)
        return result.rowcount > 0
