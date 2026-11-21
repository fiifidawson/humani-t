"""One behaviour contract for evaluation storage, run against every backend.

An evaluation row is also its job: a queued one is claimed by one worker under a lease,
claimed again when the lease lapses while attempts remain, and every change is checked
against its version. The Postgres run is skipped unless TEST_DATABASE_URL points at a
database migrated to revision 0022, as the runtime role.
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
from app.domain.evaluations import MAX_ATTEMPTS, EvaluationRecord, EvaluationStatus
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
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


async def _review(storage: StorageProvider) -> Review:
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


def _evaluation(review: Review, minutes: int = 0) -> EvaluationRecord:
    return EvaluationRecord(
        id=uuid.uuid4(),
        project_id=review.project_id,
        review_id=review.id,
        created_by=review.created_by,
        created_at=NOW + timedelta(minutes=minutes),
        judge_model="mistral-small:24b",
        question_count=2,
        system_count=1,
        status=EvaluationStatus.QUEUED,
        setup_sealed=b"setup",
    )


async def _add(storage: StorageProvider, evaluation: EvaluationRecord) -> None:
    async with storage.unit_of_work() as uow:
        await uow.evaluations.add(evaluation)
        await uow.commit()


async def _claim(
    storage: StorageProvider, worker: str, at: datetime
) -> EvaluationRecord | None:
    async with storage.unit_of_work() as uow:
        claimed = await uow.evaluations.claim_next(worker, at, LEASE)
        await uow.commit()
    return claimed


class TestEvaluationStorage:
    async def test_an_evaluation_is_read_back_as_it_was_stored(self, storage):
        evaluation = _evaluation(await _review(storage))
        await _add(storage, evaluation)

        async with storage.unit_of_work() as uow:
            assert await uow.evaluations.get(evaluation.id) == evaluation

    async def test_the_list_holds_only_the_projects_asked_for_newest_first(self, storage):
        review = await _review(storage)
        other = await _review(storage)
        older, newer = _evaluation(review), _evaluation(review, minutes=1)
        elsewhere = _evaluation(other)
        for evaluation in (older, newer, elsewhere):
            await _add(storage, evaluation)

        async with storage.unit_of_work() as uow:
            listed = await uow.evaluations.list_for_projects([review.project_id])

        assert [e.id for e in listed] == [newer.id, older.id]

    async def test_a_stale_copy_is_never_saved_over_a_newer_one(self, storage):
        evaluation = _evaluation(await _review(storage))
        await _add(storage, evaluation)
        claimed = await _claim(storage, "worker-a", NOW)
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.evaluations.save(replace(evaluation, judge_model="other"))

    async def test_progress_is_saved_with_its_results(self, storage):
        await _add(storage, _evaluation(await _review(storage)))
        claimed = await _claim(storage, "worker-a", NOW)
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            saved = await uow.evaluations.save(claimed.progressed(b"results", 1, "qwen3.5:9b"))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.evaluations.get(claimed.id) == saved
        assert (saved.answered_count, saved.results_sealed) == (1, b"results")

    async def test_one_worker_claims_a_queued_evaluation_and_the_next_finds_none(self, storage):
        review = await _review(storage)
        first, second = _evaluation(review), _evaluation(review, minutes=1)
        await _add(storage, second)
        await _add(storage, first)

        claimed = await _claim(storage, "worker-a", NOW)
        also = await _claim(storage, "worker-b", NOW)

        assert claimed is not None
        assert also is not None
        assert (claimed.id, claimed.lease_owner) == (first.id, "worker-a")
        assert also.id == second.id
        assert await _claim(storage, "worker-c", NOW) is None

    async def test_a_lapsed_lease_is_claimed_again_while_attempts_remain(self, storage):
        await _add(storage, _evaluation(await _review(storage)))
        await _claim(storage, "worker-a", NOW)
        later = NOW + LEASE + timedelta(seconds=1)

        again = await _claim(storage, "worker-b", later)

        assert again is not None
        assert (again.lease_owner, again.attempts) == ("worker-b", 2)

    async def test_renewing_needs_the_lease(self, storage):
        await _add(storage, _evaluation(await _review(storage)))
        claimed = await _claim(storage, "worker-a", NOW)
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            kept = await uow.evaluations.renew_lease(claimed.id, "worker-a", NOW + LEASE * 2)
            lost = await uow.evaluations.renew_lease(claimed.id, "worker-b", NOW + LEASE * 2)
            await uow.commit()

        assert (kept, lost) == (True, False)

    async def test_an_evaluation_out_of_attempts_is_found_to_be_failed(self, storage):
        await _add(storage, _evaluation(await _review(storage)))
        at = NOW
        for attempt in range(MAX_ATTEMPTS):
            await _claim(storage, f"worker-{attempt}", at)
            at += LEASE + timedelta(seconds=1)

        async with storage.unit_of_work() as uow:
            stuck = await uow.evaluations.lock_exhausted(at, 10)

        assert len(stuck) == 1
        assert await _claim(storage, "worker-z", at) is None
