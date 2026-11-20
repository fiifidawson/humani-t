"""PostgreSQL repositories for a person's paper marks, brief reads and dismissals.

Each row's key is the person and what they marked, and storing is `INSERT … ON CONFLICT DO
NOTHING`: marking twice, or two tabs at once, keeps the first row and never rewrites it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Row, Table, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.history import Dismissal
from app.domain.marks import BriefRead, PaperMark, PaperMarkKind
from app.storage.postgres.tables import brief_reads, needs_you_dismissals, paper_marks


async def _insert_once(
    connection: AsyncConnection, table: Table, keys: list[str], **values: object
) -> bool:
    result = await connection.execute(
        insert(table).values(**values).on_conflict_do_nothing(index_elements=keys)
    )
    return result.rowcount == 1


def _to_mark(row: Row[Any]) -> PaperMark:
    return PaperMark(
        user_id=row.user_id,
        paper_id=row.paper_id,
        kind=PaperMarkKind(row.kind),
        project_id=row.project_id,
        identity_sealed=row.identity_sealed,
        marked_at=row.marked_at,
    )


class PostgresMarkRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add_paper_mark(self, mark: PaperMark, /) -> bool:
        return await _insert_once(
            self._connection,
            paper_marks,
            ["user_id", "paper_id", "kind"],
            user_id=mark.user_id,
            paper_id=mark.paper_id,
            kind=mark.kind.value,
            project_id=mark.project_id,
            identity_sealed=mark.identity_sealed,
            marked_at=mark.marked_at,
        )

    async def remove_paper_mark(
        self, user_id: UUID, paper_id: UUID, kind: PaperMarkKind
    ) -> bool:
        result = await self._connection.execute(
            delete(paper_marks).where(
                paper_marks.c.user_id == user_id,
                paper_marks.c.paper_id == paper_id,
                paper_marks.c.kind == kind.value,
            )
        )
        return result.rowcount == 1

    async def list_paper_marks(self, user_id: UUID) -> list[PaperMark]:
        rows = await self._connection.execute(
            select(paper_marks)
            .where(paper_marks.c.user_id == user_id)
            .order_by(paper_marks.c.marked_at)
        )
        return [_to_mark(row) for row in rows]

    async def add_brief_read(self, read: BriefRead, /) -> bool:
        return await _insert_once(
            self._connection,
            brief_reads,
            ["user_id", "brief_id"],
            user_id=read.user_id,
            brief_id=read.brief_id,
            project_id=read.project_id,
            read_at=read.read_at,
        )

    async def list_brief_reads(self, user_id: UUID) -> list[BriefRead]:
        rows = await self._connection.execute(
            select(brief_reads).where(brief_reads.c.user_id == user_id)
        )
        return [BriefRead(r.user_id, r.brief_id, r.project_id, r.read_at) for r in rows]


class PostgresDismissalRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, dismissal: Dismissal, /) -> bool:
        return await _insert_once(
            self._connection,
            needs_you_dismissals,
            ["user_id", "item_key"],
            user_id=dismissal.user_id,
            item_key=dismissal.item_key,
            dismissed_at=dismissal.dismissed_at,
        )

    async def list_for_user(self, user_id: UUID) -> list[Dismissal]:
        rows = await self._connection.execute(
            select(needs_you_dismissals).where(needs_you_dismissals.c.user_id == user_id)
        )
        return [Dismissal(r.user_id, r.item_key, r.dismissed_at) for r in rows]
