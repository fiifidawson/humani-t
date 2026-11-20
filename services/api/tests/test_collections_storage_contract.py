"""One behaviour contract for collections, placements, tags and pins, run against every backend.

A collection's rows go with it; a project's placement and tags are one row each, replaced
whole; a pin is written once. The Postgres run is skipped unless TEST_DATABASE_URL points
at a database migrated to revision 0018, as the runtime role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.collections import (
    Collection,
    NoSuchCollectionError,
    Pin,
    ProjectPlacement,
    StoredTags,
)
from app.domain.errors import ConcurrentUpdateError
from app.domain.projects import Project
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
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
    await delete_all_rows(_DATABASE_URL)


async def _user(storage: StorageProvider) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:12]}@example.org",
        display_name="Test",
        password_hash="fake$x",
        created_at=NOW,
    )
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.commit()
    return user


async def _project(storage: StorageProvider, user: User) -> Project:
    project = Project(
        id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=user.id
    )
    async with storage.unit_of_work() as uow:
        await uow.projects.add(project)
        await uow.commit()
    return project


async def _collection(storage: StorageProvider, user: User, minutes: int = 0) -> Collection:
    collection = Collection(
        id=uuid.uuid4(),
        name_sealed=b"sealed-name",
        created_by=user.id,
        created_at=NOW + timedelta(minutes=minutes),
    )
    async with storage.unit_of_work() as uow:
        await uow.collections.add(collection)
        await uow.commit()
    return collection


def _placement(project: Project, collection: Collection) -> ProjectPlacement:
    return ProjectPlacement(project.id, collection.id, project.created_by, NOW)


class TestCollections:
    async def test_a_collection_is_read_back_as_stored(self, storage):
        user = await _user(storage)
        collection = await _collection(storage, user)

        async with storage.unit_of_work() as uow:
            assert await uow.collections.get(collection.id) == collection
            assert await uow.collections.list_all() == [collection]
            assert await uow.collections.lock_all() == [collection]

    async def test_every_collection_is_listed_oldest_first(self, storage):
        user = await _user(storage)
        newer = await _collection(storage, user, minutes=5)
        older = await _collection(storage, user)

        async with storage.unit_of_work() as uow:
            assert [c.id for c in await uow.collections.list_all()] == [older.id, newer.id]

    async def test_a_save_from_a_stale_copy_is_refused(self, storage):
        user = await _user(storage)
        collection = await _collection(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.collections.save(collection.renamed(b"first"))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.collections.save(collection.renamed(b"second"))

        async with storage.unit_of_work() as uow:
            stored = await uow.collections.get(collection.id)
        assert stored is not None
        assert (stored.name_sealed, stored.version) == (b"first", 1)

    async def test_deleting_one_removes_the_placements_it_held(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        collection = await _collection(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.placements.put(_placement(project, collection))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.collections.delete(collection.id) is True
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.collections.get(collection.id) is None
            assert await uow.placements.get(project.id) is None
            assert await uow.collections.delete(collection.id) is False


class TestPlacements:
    async def test_a_placement_is_replaced_whole(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        first, second = await _collection(storage, user), await _collection(storage, user)

        async with storage.unit_of_work() as uow:
            await uow.placements.put(_placement(project, first))
            await uow.placements.put(_placement(project, second))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.placements.get(project.id) == _placement(project, second)

    async def test_placements_are_listed_for_the_projects_asked_about_only(self, storage):
        user = await _user(storage)
        mine, other = await _project(storage, user), await _project(storage, user)
        collection = await _collection(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.placements.put(_placement(mine, collection))
            await uow.placements.put(_placement(other, collection))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            listed = await uow.placements.list_for_projects([mine.id])

        assert listed == [_placement(mine, collection)]

    async def test_a_placement_in_a_missing_collection_is_refused(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        gone = Collection(uuid.uuid4(), b"sealed", user.id, NOW)

        async with storage.unit_of_work() as uow:
            with pytest.raises(NoSuchCollectionError):
                await uow.placements.put(_placement(project, gone))

    async def test_removing_a_placement_says_whether_there_was_one(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        collection = await _collection(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.placements.put(_placement(project, collection))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.placements.remove(project.id) is True
            assert await uow.placements.remove(project.id) is False


class TestTags:
    async def test_tags_are_replaced_whole_and_removed(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        first = StoredTags(project.id, b"first", user.id, NOW)
        second = replace(first, tags_sealed=b"second", updated_at=NOW + timedelta(minutes=1))

        async with storage.unit_of_work() as uow:
            await uow.project_tags.put(first)
            await uow.project_tags.put(second)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.project_tags.get(project.id) == second
            assert await uow.project_tags.remove(project.id) is True
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.project_tags.get(project.id) is None


class TestPins:
    async def test_a_pin_is_written_once(self, storage):
        user = await _user(storage)
        project = await _project(storage, user)
        pin = Pin(user.id, project.id, NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.pins.add(pin) is True
            assert await uow.pins.add(replace(pin, pinned_at=NOW + timedelta(hours=1))) is False
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.pins.get(user.id, project.id) == pin

    async def test_a_users_pins_are_listed_newest_first(self, storage):
        user = await _user(storage)
        older, newer = await _project(storage, user), await _project(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.pins.add(Pin(user.id, older.id, NOW))
            await uow.pins.add(Pin(user.id, newer.id, NOW + timedelta(minutes=1)))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            pins = await uow.pins.list_for_user(user.id)

        assert [p.project_id for p in pins] == [newer.id, older.id]

    async def test_pins_are_removed_one_at_a_time_or_with_their_project(self, storage):
        user, other = await _user(storage), await _user(storage)
        project = await _project(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.pins.add(Pin(user.id, project.id, NOW))
            await uow.pins.add(Pin(other.id, project.id, NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.pins.remove(user.id, project.id) is True
            assert await uow.pins.remove(user.id, project.id) is False
            assert await uow.pins.remove_for_project(project.id) == 1
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.pins.list_for_user(other.id) == []
