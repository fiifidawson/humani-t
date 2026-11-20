"""PostgreSQL repository for leanings ("Your view first").

`(run_id, user_id)` is the primary key, and storing is `INSERT … ON CONFLICT DO NOTHING`:
of two answers sent at once, the second learns it lost and the first stays.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.leanings import StoredLeaning
from app.storage.postgres.tables import leanings


class PostgresLeaningRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, leaning: StoredLeaning, /) -> bool:
        result = await self._connection.execute(
            insert(leanings)
            .values(
                run_id=leaning.run_id,
                user_id=leaning.user_id,
                project_id=leaning.project_id,
                option_sealed=leaning.option_sealed,
                answered_at=leaning.answered_at,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "user_id"])
        )
        return result.rowcount == 1

    async def get(self, run_id: UUID, user_id: UUID) -> StoredLeaning | None:
        row = (
            await self._connection.execute(
                select(leanings).where(
                    leanings.c.run_id == run_id, leanings.c.user_id == user_id
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return StoredLeaning(
            project_id=row.project_id,
            run_id=row.run_id,
            user_id=row.user_id,
            option_sealed=row.option_sealed,
            answered_at=row.answered_at,
        )
