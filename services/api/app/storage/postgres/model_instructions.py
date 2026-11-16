"""PostgreSQL repository for a project's model instruction versions.

`(project_id, version)` is the primary key, and storing is `INSERT … ON CONFLICT DO
NOTHING`: of two owners saving at once, the second learns it lost.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.model_instructions import StoredModelInstructions
from app.storage.postgres.tables import model_instruction_versions as versions


def _to_version(row: Row[Any]) -> StoredModelInstructions:
    return StoredModelInstructions(
        project_id=row.project_id,
        version=row.version,
        instructions_sealed=row.instructions_sealed,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _of(project_id: UUID) -> Select[Any]:
    return select(versions).where(versions.c.project_id == project_id)


class PostgresModelInstructionsRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, version: StoredModelInstructions, /) -> bool:
        result = await self._connection.execute(
            insert(versions)
            .values(
                project_id=version.project_id,
                version=version.version,
                instructions_sealed=version.instructions_sealed,
                created_by=version.created_by,
                created_at=version.created_at,
            )
            .on_conflict_do_nothing(index_elements=["project_id", "version"])
        )
        return result.rowcount == 1

    async def latest(self, project_id: UUID) -> StoredModelInstructions | None:
        return await self._first(_of(project_id))

    async def in_force(self, project_id: UUID, at: datetime) -> StoredModelInstructions | None:
        return await self._first(_of(project_id).where(versions.c.created_at <= at))

    async def list_for_project(self, project_id: UUID) -> list[StoredModelInstructions]:
        rows = await self._connection.execute(_of(project_id).order_by(versions.c.version))
        return [_to_version(row) for row in rows]

    async def _first(self, query: Select[Any]) -> StoredModelInstructions | None:
        row = (
            await self._connection.execute(query.order_by(versions.c.version.desc()).limit(1))
        ).one_or_none()
        return None if row is None else _to_version(row)
