"""One behaviour contract for decision brief storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a database
migrated to revision 0011, connected as the runtime role.

What matters (architecture §4.2, §5.7): a run has at most one brief, however
many times its stage runs or however many workers race to store it; the first
stored wins and the others learn they lost.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.briefs import BriefRecord, StopReason
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.domain.runs import Run, RunState
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
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


async def _review(storage: StorageProvider) -> Review:
    """A committed user, project and review: the parents a brief needs."""
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
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.projects.add(project)
        await uow.reviews.add(review)
        await uow.commit()
    return review


async def _run(storage: StorageProvider, review: Review, minutes: int = 0) -> Run:
    at = NOW + timedelta(minutes=minutes)
    run = Run(
        id=uuid.uuid4(),
        review_id=review.id,
        project_id=review.project_id,
        stages=("answer",),
        state=RunState.QUEUED,
        created_at=at,
        created_by=review.created_by,
        queued_at=at,
    )
    async with storage.unit_of_work() as uow:
        await uow.runs.add(run)
        await uow.commit()
    return run


def _record(run: Run, version: int = 1, minutes: int = 0) -> BriefRecord:
    return BriefRecord(
        id=uuid.uuid4(),
        project_id=run.project_id,
        review_id=run.review_id,
        run_id=run.id,
        version=version,
        created_at=NOW + timedelta(minutes=minutes),
        model_id="qwen3.5:9b",
        rounds=2,
        kept_round=2,
        max_rounds=3,
        stopped_because=StopReason.ACCEPTED,
        content_sealed=b"content",
        evidence_sealed=b"evidence",
        trace_sealed=b"trace",
    )


async def _add(storage: StorageProvider, record: BriefRecord) -> bool:
    async with storage.unit_of_work() as uow:
        stored = await uow.briefs.add(record)
        await uow.commit()
    return stored


class TestBriefStorage:
    async def test_a_stored_brief_is_read_back_by_id_and_by_run(self, storage):
        review = await _review(storage)
        record = _record(await _run(storage, review))

        assert await _add(storage, record)

        async with storage.unit_of_work() as uow:
            assert await uow.briefs.get(record.id) == record
            assert await uow.briefs.for_run(record.run_id) == record

    async def test_a_run_keeps_its_first_brief_and_a_second_learns_it_lost(self, storage):
        review = await _review(storage)
        run = await _run(storage, review)
        first = _record(run)

        assert await _add(storage, first)
        assert not await _add(storage, replace(_record(run), content_sealed=b"other"))

        async with storage.unit_of_work() as uow:
            assert await uow.briefs.for_run(run.id) == first

    async def test_the_latest_brief_of_a_review_is_its_highest_version(self, storage):
        review = await _review(storage)
        older = _record(await _run(storage, review), version=1)
        newer = _record(await _run(storage, review, minutes=5), version=2, minutes=5)
        await _add(storage, older)
        await _add(storage, newer)

        async with storage.unit_of_work() as uow:
            assert await uow.briefs.latest_for_review(review.id) == newer
            assert await uow.briefs.count_for_review(review.id) == 2

    async def test_a_review_without_a_brief_has_none(self, storage):
        review = await _review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.briefs.latest_for_review(review.id) is None
            assert await uow.briefs.count_for_review(review.id) == 0
            assert await uow.briefs.get(uuid.uuid4()) is None

    async def test_an_uncommitted_brief_is_not_stored(self, storage):
        review = await _review(storage)
        record = _record(await _run(storage, review))

        async with storage.unit_of_work() as uow:
            await uow.briefs.add(record)

        async with storage.unit_of_work() as uow:
            assert await uow.briefs.get(record.id) is None
