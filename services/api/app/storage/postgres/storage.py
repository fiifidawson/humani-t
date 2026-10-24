"""PostgreSQL storage via SQLAlchemy 2 (async) and asyncpg."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Table, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.domain.accounts import Invitation, PasswordReset, User
from app.domain.audit import AuditEntry, AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.domain.sessions import Session
from app.storage.postgres.tables import (
    audit_chain_head,
    audit_events,
    data_keys,
    invitations,
    password_resets,
    sessions,
    users,
)


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


class _RecordTable[T]:
    """Mapping for domain types whose fields match their table's columns one to one.

    Explicit per-type mapping (as for audit events above) is clearer when the
    shapes differ; when they match, this keeps four repositories from being
    four copies of the same code.
    """

    def __init__(self, connection: AsyncConnection, table: Table, record: type[T]) -> None:
        self._connection = connection
        self._table = table
        self._record = record

    def _from_row(self, row: Row[Any]) -> T:
        return self._record(**row._mapping)

    async def _one(self, **where: object) -> T | None:
        query = select(self._table).where(
            *(self._table.c[name] == value for name, value in where.items())
        )
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else self._from_row(row)

    async def _many(self, *conditions: Any) -> list[T]:
        result = await self._connection.execute(select(self._table).where(*conditions))
        return [self._from_row(row) for row in result]

    async def add(self, record: T) -> None:
        await self._connection.execute(
            insert(self._table).values(**dataclasses.asdict(record))  # type: ignore[call-overload]
        )

    async def save(self, record: T) -> None:
        values = dataclasses.asdict(record)  # type: ignore[call-overload]
        result = await self._connection.execute(
            update(self._table).where(self._table.c.id == values["id"]).values(**values)
        )
        if result.rowcount != 1:
            raise ValueError(f"{self._table.name} row {values['id']} does not exist")


class PostgresUserRepository(_RecordTable[User]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, users, User)

    async def get(self, user_id: UUID) -> User | None:
        return await self._one(id=user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self._one(email=email)


class PostgresSessionRepository(_RecordTable[Session]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, sessions, Session)

    async def get(self, session_id: UUID) -> Session | None:
        return await self._one(id=session_id)

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        return await self._one(token_hash=token_hash)

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        return await self._many(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))


class PostgresInvitationRepository(_RecordTable[Invitation]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, invitations, Invitation)

    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None:
        return await self._one(token_hash=token_hash)


class PostgresPasswordResetRepository(_RecordTable[PasswordReset]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, password_resets, PasswordReset)

    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None:
        return await self._one(token_hash=token_hash)

    async def list_unused_for_user(self, user_id: UUID) -> list[PasswordReset]:
        return await self._many(
            password_resets.c.user_id == user_id, password_resets.c.used_at.is_(None)
        )


class PostgresUnitOfWork:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.data_keys = PostgresDataKeyRepository(connection)
        self.audit = PostgresAuditRepository(connection)
        self.users = PostgresUserRepository(connection)
        self.sessions = PostgresSessionRepository(connection)
        self.invitations = PostgresInvitationRepository(connection)
        self.password_resets = PostgresPasswordResetRepository(connection)

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
