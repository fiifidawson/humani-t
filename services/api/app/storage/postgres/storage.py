"""PostgreSQL storage via SQLAlchemy 2 (async) and asyncpg."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import Row, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.domain.encryption import WrappedDataKey
from app.storage.postgres.tables import data_keys


def _to_data_key(row: Row[tuple[UUID, int, bytes | None, object, object]]) -> WrappedDataKey:
    return WrappedDataKey(
        scope_id=row.scope_id,
        kek_version=row.kek_version,
        wrapped=row.wrapped,
        created_at=row.created_at,
        destroyed_at=row.destroyed_at,
    )


class PostgresDataKeyRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        result = await self._connection.execute(
            select(data_keys).where(data_keys.c.scope_id == scope_id)
        )
        row = result.one_or_none()
        return None if row is None else _to_data_key(row)

    async def add(self, key: WrappedDataKey) -> None:
        await self._connection.execute(
            insert(data_keys).values(
                scope_id=key.scope_id,
                kek_version=key.kek_version,
                wrapped=key.wrapped,
                created_at=key.created_at,
                destroyed_at=key.destroyed_at,
            )
        )

    async def save(self, key: WrappedDataKey) -> None:
        result = await self._connection.execute(
            update(data_keys)
            .where(data_keys.c.scope_id == key.scope_id)
            .values(
                kek_version=key.kek_version,
                wrapped=key.wrapped,
                destroyed_at=key.destroyed_at,
            )
        )
        if result.rowcount != 1:
            raise ValueError(f"data key for scope {key.scope_id} does not exist")

    async def list_live(self) -> list[WrappedDataKey]:
        result = await self._connection.execute(
            select(data_keys).where(data_keys.c.wrapped.is_not(None))
        )
        return [_to_data_key(row) for row in result]


class PostgresUnitOfWork:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.data_keys = PostgresDataKeyRepository(connection)

    async def commit(self) -> None:
        await self._connection.commit()

    async def rollback(self) -> None:
        await self._connection.rollback()


class PostgresStorage:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, url: str) -> PostgresStorage:
        # pool_pre_ping drops connections Postgres closed while idle, instead of
        # failing the first request after a database restart.
        return cls(create_async_engine(url, pool_pre_ping=True))

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[PostgresUnitOfWork]:
        async with self._engine.connect() as connection:
            unit = PostgresUnitOfWork(connection)
            try:
                yield unit
            finally:
                # Uncommitted work is discarded, matching the in-memory version.
                await connection.rollback()

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True

    async def close(self) -> None:
        await self._engine.dispose()
