"""One behaviour contract for a project's instruction versions, run against every backend.

Versions are written once: of two owners saving at the same moment, the second learns it
lost rather than overwriting the first. The Postgres run is skipped unless
TEST_DATABASE_URL points at a database migrated to revision 0017, as the runtime role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.model_instructions import StoredModelInstructions
from app.domain.projects import Project
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
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


async def _project(storage: StorageProvider) -> Project:
    """A committed user and project: the parents a version needs."""
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:12]}@example.org",
        display_name="Test",
        password_hash="fake$x",
        created_at=NOW,
    )
    project = Project(
        id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=user.id
    )
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.projects.add(project)
        await uow.commit()
    return project


def _version(project: Project, version: int = 1, minutes: int = 0) -> StoredModelInstructions:
    return StoredModelInstructions(
        project_id=project.id,
        version=version,
        instructions_sealed=f"sealed-{version}".encode(),
        created_by=project.created_by,
        created_at=NOW + timedelta(minutes=minutes),
    )


async def _add(storage: StorageProvider, version: StoredModelInstructions) -> bool:
    async with storage.unit_of_work() as uow:
        stored = await uow.model_instructions.add(version)
        await uow.commit()
    return stored


class TestModelInstructionsStorage:
    async def test_a_stored_version_is_read_back_as_the_latest(self, storage):
        project = await _project(storage)
        version = _version(project)

        assert await _add(storage, version)

        async with storage.unit_of_work() as uow:
            assert await uow.model_instructions.latest(project.id) == version

    async def test_the_latest_is_the_highest_version_and_all_are_listed_oldest_first(
        self, storage
    ):
        project = await _project(storage)
        first, second = _version(project, 1), _version(project, 2, minutes=5)
        await _add(storage, first)
        await _add(storage, second)

        async with storage.unit_of_work() as uow:
            assert await uow.model_instructions.latest(project.id) == second
            assert await uow.model_instructions.list_for_project(project.id) == [first, second]

    async def test_the_version_in_force_at_a_moment_is_the_last_saved_by_then(self, storage):
        project = await _project(storage)
        first, second = _version(project, 1), _version(project, 2, minutes=5)
        await _add(storage, first)
        await _add(storage, second)

        async with storage.unit_of_work() as uow:
            instructions = uow.model_instructions
            assert await instructions.in_force(project.id, NOW - timedelta(minutes=1)) is None
            assert await instructions.in_force(project.id, NOW) == first
            assert await instructions.in_force(project.id, NOW + timedelta(minutes=4)) == first
            assert await instructions.in_force(project.id, NOW + timedelta(minutes=5)) == second

    async def test_a_second_version_with_the_same_number_learns_it_lost(self, storage):
        project = await _project(storage)
        first = _version(project)
        await _add(storage, first)

        assert not await _add(storage, replace(first, instructions_sealed=b"other"))

        async with storage.unit_of_work() as uow:
            assert await uow.model_instructions.latest(project.id) == first

    async def test_a_project_without_instructions_has_none(self, storage):
        project = await _project(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.model_instructions.latest(project.id) is None
            assert await uow.model_instructions.list_for_project(project.id) == []

    async def test_an_uncommitted_version_is_not_stored(self, storage):
        project = await _project(storage)

        async with storage.unit_of_work() as uow:
            await uow.model_instructions.add(_version(project))

        async with storage.unit_of_work() as uow:
            assert await uow.model_instructions.latest(project.id) is None
