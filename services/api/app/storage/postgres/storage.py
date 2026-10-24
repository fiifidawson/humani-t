"""PostgreSQL storage via SQLAlchemy 2 (async) and asyncpg."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Row, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.domain.audit import AuditEntry, AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.storage.postgres.tables import audit_chain_head, audit_events, data_keys


def _to_data_key(row: Row[Any]) -> WrappedDataKey:
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


def _to_audit_event(row: Row[Any]) -> AuditEvent:
    return AuditEvent(
        entry=AuditEntry(
            id=row.id,
            occurred_at=row.occurred_at,
            action=row.action,
            actor_id=row.actor_id,
            target_type=row.target_type,
            target_id=row.target_id,
            scope_id=row.scope_id,
            request_id=row.request_id,
            details_sealed=row.details_sealed,
        ),
        sequence=row.sequence,
        previous_hash=row.previous_hash,
        hash=row.hash,
    )


class PostgresAuditRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def lock_head(self) -> ChainHead:
        result = await self._connection.execute(
            select(audit_chain_head.c.sequence, audit_chain_head.c.hash)
            .where(audit_chain_head.c.id == 1)
            .with_for_update()
        )
        row = result.one()
        return ChainHead(row.sequence, row.hash)

    async def append(self, event: AuditEvent) -> None:
        entry = event.entry
        await self._connection.execute(
            insert(audit_events).values(
                sequence=event.sequence,
                id=entry.id,
                occurred_at=entry.occurred_at,
                action=entry.action,
                actor_id=entry.actor_id,
                target_type=entry.target_type,
                target_id=entry.target_id,
                scope_id=entry.scope_id,
                request_id=entry.request_id,
                details_sealed=entry.details_sealed,
                previous_hash=event.previous_hash,
                hash=event.hash,
            )
        )
        # The WHERE re-checks the head the event was chained onto, so an append
        # without the lock fails loudly instead of forking the chain.
        result = await self._connection.execute(
            update(audit_chain_head)
            .where(
                audit_chain_head.c.id == 1,
                audit_chain_head.c.sequence == event.sequence - 1,
                audit_chain_head.c.hash == event.previous_hash,
            )
            .values(sequence=event.sequence, hash=event.hash)
        )
        if result.rowcount != 1:
            raise ValueError("audit event does not extend the current chain head")

    async def head(self) -> ChainHead:
        result = await self._connection.execute(
            select(audit_chain_head.c.sequence, audit_chain_head.c.hash).where(
                audit_chain_head.c.id == 1
            )
        )
        row = result.one()
        return ChainHead(row.sequence, row.hash)

    async def list_events(self) -> list[AuditEvent]:
        result = await self._connection.execute(
            select(audit_events).order_by(audit_events.c.sequence)
        )
        return [_to_audit_event(row) for row in result]


class PostgresUnitOfWork:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.data_keys = PostgresDataKeyRepository(connection)
        self.audit = PostgresAuditRepository(connection)

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
