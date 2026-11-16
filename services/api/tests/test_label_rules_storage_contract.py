"""One behaviour contract for label rule versions, run against every backend.

Versions are written once: of two people applying rules at the same moment, the second
learns it lost rather than overwriting the first. The Postgres run is skipped unless
TEST_DATABASE_URL points at a database migrated to revision 0015, as the runtime role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.label_rules import LabelRules, LabelRulesVersion, UnclearCountsAs
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
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
    """A committed user, project and review: the parents a rules version needs."""
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


def _version(review: Review, version: int = 1, minutes: int = 0) -> LabelRulesVersion:
    return LabelRulesVersion(
        review_id=review.id,
        project_id=review.project_id,
        version=version,
        rules=LabelRules(possibly_min_met=2, unclear_counts_as=UnclearCountsAs.UNCERTAIN),
        created_by=review.created_by,
        created_at=NOW + timedelta(minutes=minutes),
        papers_moved=4,
    )


async def _add(storage: StorageProvider, version: LabelRulesVersion) -> bool:
    async with storage.unit_of_work() as uow:
        stored = await uow.label_rules.add(version)
        await uow.commit()
    return stored


class TestLabelRulesStorage:
    async def test_a_stored_version_is_read_back_as_the_latest(self, storage):
        review = await _review(storage)
        version = _version(review)

        assert await _add(storage, version)

        async with storage.unit_of_work() as uow:
            assert await uow.label_rules.latest(review.id) == version

    async def test_the_latest_is_the_highest_version_and_all_are_listed_oldest_first(
        self, storage
    ):
        review = await _review(storage)
        first, second = _version(review, 1), _version(review, 2, minutes=5)
        await _add(storage, first)
        await _add(storage, second)

        async with storage.unit_of_work() as uow:
            assert await uow.label_rules.latest(review.id) == second
            assert await uow.label_rules.list_for_review(review.id) == [first, second]

    async def test_a_second_version_with_the_same_number_learns_it_lost(self, storage):
        review = await _review(storage)
        first = _version(review)
        await _add(storage, first)

        assert not await _add(storage, replace(first, papers_moved=9))

        async with storage.unit_of_work() as uow:
            assert await uow.label_rules.latest(review.id) == first

    async def test_a_review_without_rules_has_none(self, storage):
        review = await _review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.label_rules.latest(review.id) is None
            assert await uow.label_rules.list_for_review(review.id) == []

    async def test_an_uncommitted_version_is_not_stored(self, storage):
        review = await _review(storage)

        async with storage.unit_of_work() as uow:
            await uow.label_rules.add(_version(review))

        async with storage.unit_of_work() as uow:
            assert await uow.label_rules.latest(review.id) is None
