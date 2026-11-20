"""One behaviour contract for leanings, run against every backend.

A member answers once per run: of two answers racing, the second learns it lost rather
than overwriting the first. The Postgres run is skipped unless TEST_DATABASE_URL points
at a database migrated to revision 0020, connected as the runtime role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.domain.accounts import User
from app.domain.leanings import StoredLeaning
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.domain.runs import Run, RunState
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


async def _run(storage: StorageProvider, user: User) -> Run:
    """A committed project, review and run: the parents a leaning needs."""
    project = Project(
        id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=user.id
    )
    review = Review(
        id=uuid.uuid4(),
        project_id=project.id,
        question_sealed=b"sealed",
        status=ReviewStatus.NEW,
        created_at=NOW,
        created_by=user.id,
    )
    run = Run(
        id=uuid.uuid4(),
        review_id=review.id,
        project_id=project.id,
        stages=("answer",),
        state=RunState.QUEUED,
        created_at=NOW,
        created_by=user.id,
        queued_at=NOW,
    )
    async with storage.unit_of_work() as uow:
        await uow.projects.add(project)
        await uow.reviews.add(review)
        await uow.runs.add(run)
        await uow.commit()
    return run


def _leaning(run: Run, user: User, sealed: bytes = b"sealed-cash") -> StoredLeaning:
    return StoredLeaning(
        project_id=run.project_id,
        run_id=run.id,
        user_id=user.id,
        option_sealed=sealed,
        answered_at=NOW,
    )


class TestLeanings:
    async def test_a_stored_leaning_reads_back_as_written(self, storage):
        user = await _user(storage)
        run = await _run(storage, user)

        async with storage.unit_of_work() as uow:
            assert await uow.leanings.add(_leaning(run, user)) is True
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.leanings.get(run.id, user.id)

        assert stored == _leaning(run, user)

    async def test_nothing_is_found_before_the_member_answers(self, storage):
        user = await _user(storage)
        run = await _run(storage, user)

        async with storage.unit_of_work() as uow:
            assert await uow.leanings.get(run.id, user.id) is None

    async def test_a_second_answer_loses_and_the_first_stays(self, storage):
        user = await _user(storage)
        run = await _run(storage, user)
        async with storage.unit_of_work() as uow:
            await uow.leanings.add(_leaning(run, user))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.leanings.add(_leaning(run, user, b"sealed-other")) is False
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.leanings.get(run.id, user.id)

        assert stored is not None
        assert stored.option_sealed == b"sealed-cash"

    async def test_each_member_has_their_own_answer(self, storage):
        first = await _user(storage)
        second = await _user(storage)
        run = await _run(storage, first)

        async with storage.unit_of_work() as uow:
            await uow.leanings.add(_leaning(run, first))
            assert await uow.leanings.add(_leaning(run, second, b"sealed-other")) is True
            await uow.commit()
        async with storage.unit_of_work() as uow:
            mine = await uow.leanings.get(run.id, second.id)

        assert mine is not None
        assert mine.option_sealed == b"sealed-other"

    async def test_an_uncommitted_answer_is_not_kept(self, storage):
        user = await _user(storage)
        run = await _run(storage, user)

        async with storage.unit_of_work() as uow:
            await uow.leanings.add(_leaning(run, user))
        async with storage.unit_of_work() as uow:
            assert await uow.leanings.get(run.id, user.id) is None


class TestTheProjectSetting:
    async def test_the_your_view_first_switch_is_kept(self, storage):
        user = await _user(storage)
        run = await _run(storage, user)
        async with storage.unit_of_work() as uow:
            project = await uow.projects.get(run.project_id)
            assert project is not None
            assert project.is_view_first_on is False
            await uow.projects.save(project.with_view_first(is_on=True))
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.projects.get(run.project_id)

        assert stored is not None
        assert stored.is_view_first_on is True
