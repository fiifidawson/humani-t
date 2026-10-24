"""One behaviour contract, run against every StorageProvider implementation.

The Postgres run is skipped unless TEST_DATABASE_URL points at a migrated
database as the runtime role. See CONTRIBUTING for how to start one.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.encryption import WrappedDataKey
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.postgres,
                pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set"),
            ],
        ),
    ]
)
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[StorageProvider]:
    if request.param == "memory":
        yield MemoryStorage()
        return
    assert _DATABASE_URL is not None
    postgres = PostgresStorage.from_url(_DATABASE_URL)
    yield postgres
    await postgres.close()
    await _truncate(_DATABASE_URL)


async def _truncate(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM data_keys"))
    await engine.dispose()


def _key(scope_id: uuid.UUID | None = None) -> WrappedDataKey:
    return WrappedDataKey(
        scope_id=scope_id or uuid.uuid4(), kek_version=1, wrapped=b"wrapped", created_at=NOW
    )


class TestDataKeys:
    async def test_committed_key_is_visible_to_the_next_unit_of_work(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(key.scope_id) == key

    async def test_uncommitted_work_is_discarded(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)

        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(key.scope_id) is None

    async def test_rollback_discards_changes(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.rollback()
            assert await uow.data_keys.get(key.scope_id) is None

    async def test_missing_scope_is_none(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(uuid.uuid4()) is None

    async def test_destroying_a_key_is_saved(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.data_keys.save(key.destroyed(NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            stored = await uow.data_keys.get(key.scope_id)
            live = await uow.data_keys.list_live()

        assert stored is not None
        assert stored.is_destroyed
        assert key.scope_id not in {k.scope_id for k in live}

    async def test_saving_an_unknown_key_fails(self, storage):
        async with storage.unit_of_work() as uow:
            with pytest.raises(ValueError, match="does not exist"):
                await uow.data_keys.save(_key())
