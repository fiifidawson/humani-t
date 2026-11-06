"""One behaviour contract for follow-up question storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a database
migrated to revision 0014, connected as the runtime role.

What matters (architecture §5.7): a thread's position is taken once; a waiting
question is claimed by one worker under a lease; a lapsed lease is claimed
again while attempts remain and is found exhausted after; no blind overwrites.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.errors import ConcurrentUpdateError
from app.domain.projects import Project
from app.domain.questions import QuestionRecord, QuestionStatus, Thread
from app.domain.reviews import Review, ReviewStatus
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
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


async def _thread(storage: StorageProvider) -> Thread:
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
    review = Review(
        id=uuid.uuid4(),
        project_id=project.id,
        question_sealed=b"sealed",
        status=ReviewStatus.NEW,
        created_at=NOW,
        created_by=user.id,
    )
    thread = Thread(uuid.uuid4(), project.id, review.id, NOW, user.id)
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.projects.add(project)
        await uow.reviews.add(review)
        await uow.questions.add_thread(thread)
        await uow.commit()
    return thread


def _question(thread: Thread, position: int = 1, minutes: int = 0) -> QuestionRecord:
    return QuestionRecord(
        id=uuid.uuid4(),
        thread_id=thread.id,
        project_id=thread.project_id,
        review_id=thread.review_id,
        position=position,
        asked_by=thread.created_by,
        asked_at=NOW + timedelta(minutes=minutes),
        status=QuestionStatus.PENDING,
        question_sealed=b"sealed",
    )


async def _add(storage: StorageProvider, question: QuestionRecord) -> None:
    async with storage.unit_of_work() as uow:
        await uow.questions.add(question)
        await uow.commit()


async def _claim(storage: StorageProvider, worker: str, at: datetime) -> QuestionRecord | None:
    async with storage.unit_of_work() as uow:
        claimed = await uow.questions.claim_next(worker, at, LEASE)
        await uow.commit()
    return claimed


class TestQuestionStorage:
    async def test_a_thread_and_its_questions_are_read_back_in_order(self, storage):
        thread = await _thread(storage)
        second, first = _question(thread, 2, 1), _question(thread, 1)
        await _add(storage, first)
        await _add(storage, second)

        async with storage.unit_of_work() as uow:
            assert await uow.questions.get_thread(thread.id) == thread
            assert await uow.questions.list_threads(thread.review_id) == [thread]
            assert [q.id for q in await uow.questions.list_for_thread(thread.id)] == [
                first.id,
                second.id,
            ]

    async def test_a_position_in_a_thread_is_taken_once(self, storage):
        thread = await _thread(storage)
        await _add(storage, _question(thread))

        with pytest.raises(ConcurrentUpdateError):
            await _add(storage, _question(thread))

    async def test_the_oldest_waiting_question_is_claimed_once(self, storage):
        thread = await _thread(storage)
        older = _question(thread)
        await _add(storage, older)

        claimed = await _claim(storage, "a", NOW)
        again = await _claim(storage, "b", NOW)

        assert claimed is not None
        assert (claimed.id, claimed.status, claimed.lease_owner) == (
            older.id,
            QuestionStatus.ANSWERING,
            "a",
        )
        assert again is None

    async def test_a_lapsed_lease_is_claimed_again_then_found_exhausted(self, storage):
        thread = await _thread(storage)
        await _add(storage, _question(thread))
        await _claim(storage, "a", NOW)
        later = NOW + LEASE + timedelta(seconds=1)

        second = await _claim(storage, "b", later)
        exhausted_at = later + LEASE + timedelta(seconds=1)

        assert second is not None
        assert second.attempts == 2
        assert await _claim(storage, "c", exhausted_at) is None
        async with storage.unit_of_work() as uow:
            assert [q.id for q in await uow.questions.lock_exhausted(exhausted_at, 10)] == [
                second.id
            ]

    async def test_only_the_lease_holder_renews(self, storage):
        thread = await _thread(storage)
        await _add(storage, _question(thread))
        claimed = await _claim(storage, "a", NOW)
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            assert await uow.questions.renew_lease(claimed.id, "a", NOW + LEASE * 2)
            assert not await uow.questions.renew_lease(claimed.id, "b", NOW + LEASE * 2)
            await uow.commit()

    async def test_a_stale_copy_cannot_overwrite_a_newer_one(self, storage):
        thread = await _thread(storage)
        question = _question(thread)
        await _add(storage, question)
        await _claim(storage, "a", NOW)

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.questions.save(replace(question, status=QuestionStatus.PENDING))
