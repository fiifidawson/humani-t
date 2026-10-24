"""In-memory storage for tests and for running the API without a database.

Transactions are real: changes are staged on a copy and only replace the shared
state on `commit`, so a test that forgets to commit fails the same way it would
against Postgres. Concurrent transactions are not isolated from each other —
last commit wins — which is acceptable for tests and single-user development.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from app.domain.audit import GENESIS, AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey


class _MemoryState:
    def __init__(self) -> None:
        self.data_keys: dict[UUID, WrappedDataKey] = {}
        self.audit_events: list[AuditEvent] = []
        self.audit_head: ChainHead = GENESIS

    def copy(self) -> _MemoryState:
        clone = _MemoryState()
        clone.data_keys = dict(self.data_keys)
        clone.audit_events = list(self.audit_events)
        clone.audit_head = self.audit_head
        return clone


class MemoryDataKeyRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        return self._state.data_keys.get(scope_id)

    async def add(self, key: WrappedDataKey) -> None:
        if key.scope_id in self._state.data_keys:
            raise ValueError(f"data key for scope {key.scope_id} already exists")
        self._state.data_keys[key.scope_id] = key

    async def save(self, key: WrappedDataKey) -> None:
        if key.scope_id not in self._state.data_keys:
            raise ValueError(f"data key for scope {key.scope_id} does not exist")
        self._state.data_keys[key.scope_id] = key

    async def list_live(self) -> list[WrappedDataKey]:
        return [k for k in self._state.data_keys.values() if not k.is_destroyed]


class MemoryAuditRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def lock_head(self) -> ChainHead:
        return self._state.audit_head

    async def append(self, event: AuditEvent) -> None:
        head = self._state.audit_head
        if event.sequence != head.sequence + 1 or event.previous_hash != head.hash:
            raise ValueError("audit event does not extend the current chain head")
        self._state.audit_events.append(event)
        self._state.audit_head = ChainHead(event.sequence, event.hash)

    async def head(self) -> ChainHead:
        return self._state.audit_head

    async def list_events(self) -> list[AuditEvent]:
        return list(self._state.audit_events)


class MemoryUnitOfWork:
    def __init__(self, storage: MemoryStorage) -> None:
        self._storage = storage
        self._stage(storage.state.copy())

    def _stage(self, state: _MemoryState) -> None:
        self._staged = state
        self.data_keys = MemoryDataKeyRepository(state)
        self.audit = MemoryAuditRepository(state)

    async def commit(self) -> None:
        self._storage.state = self._staged
        self._stage(self._staged.copy())

    async def rollback(self) -> None:
        self._stage(self._storage.state.copy())


class MemoryStorage:
    def __init__(self) -> None:
        self.state = _MemoryState()

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[MemoryUnitOfWork]:
        yield MemoryUnitOfWork(self)

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None
