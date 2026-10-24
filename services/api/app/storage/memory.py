"""In-memory storage for tests and for running the API without a database.

Transactions are real: changes are staged on a copy and only replace the shared
state on `commit`, so a test that forgets to commit fails the same way it would
against Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from app.domain.encryption import WrappedDataKey


class _MemoryState:
    def __init__(self) -> None:
        self.data_keys: dict[UUID, WrappedDataKey] = {}

    def copy(self) -> _MemoryState:
        clone = _MemoryState()
        clone.data_keys = dict(self.data_keys)
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


class MemoryUnitOfWork:
    def __init__(self, storage: MemoryStorage) -> None:
        self._storage = storage
        self._staged = storage.state.copy()
        self.data_keys = MemoryDataKeyRepository(self._staged)

    async def commit(self) -> None:
        self._storage.state = self._staged
        self._staged = self._staged.copy()
        self.data_keys = MemoryDataKeyRepository(self._staged)

    async def rollback(self) -> None:
        self._staged = self._storage.state.copy()
        self.data_keys = MemoryDataKeyRepository(self._staged)


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
