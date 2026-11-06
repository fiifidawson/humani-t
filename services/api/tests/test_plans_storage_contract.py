"""One behaviour contract for plan storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a database
migrated to revision 0008 and is used as the runtime role, as in
`test_runs_storage_contract.py`.

What matters most (architecture §4.3 and §5.7):

- a plan version is written once: two writers drafting "the next version"
  cannot both succeed, and an accepted version is never rewritten;
- accepting is conditional: the first accept wins, a second learns it lost,
  and only the latest version can be accepted;
- no blind overwrites: a stale copy of the conversation cannot replace a newer one.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.accounts import User
from app.domain.errors import ConcurrentUpdateError
from app.domain.plans import (
    ModelCallPurpose,
    PlanAuthor,
    PlanMode,
    StoredConversation,
    StoredPlanVersion,
    StoredReceipt,
)
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.domain.vocabulary import VocabularyOrigin
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


async def _stored_review(storage: StorageProvider) -> tuple[User, Review]:
    """A committed user, project and review: the parents every plan row needs."""
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
        question_sealed=b"sealed-question",
        status=ReviewStatus.NEW,
        created_at=NOW,
        created_by=user.id,
    )
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.projects.add(project)
        await uow.reviews.add(review)
        await uow.commit()
    return user, review


def _conversation(review: Review, user: User) -> StoredConversation:
    return StoredConversation(
        review_id=review.id,
        project_id=review.project_id,
        mode=PlanMode.DEEP,
        created_at=NOW,
        created_by=user.id,
        body_sealed=b"sealed-conversation",
    )


def _version(
    review: Review,
    version: int,
    *,
    author: PlanAuthor | None = None,
    origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED,
) -> StoredPlanVersion:
    return StoredPlanVersion(
        review_id=review.id,
        project_id=review.project_id,
        version=version,
        origin=origin,
        author=author or PlanAuthor.model(),
        created_at=NOW + timedelta(minutes=version),
        body_sealed=f"sealed-version-{version}".encode(),
    )


def _receipt(review: Review, *, at: datetime, user: User | None) -> StoredReceipt:
    return StoredReceipt(
        id=uuid.uuid4(),
        review_id=review.id,
        project_id=review.project_id,
        purpose=ModelCallPurpose.DRAFT,
        created_at=at,
        created_by=None if user is None else user.id,
        model_id="llama3.1:8b",
        prompt_version="plan.draft@1",
        is_delivered=True,
        body_sealed=b"sealed-receipt",
    )


async def _add_versions(storage: StorageProvider, review: Review, *numbers: int) -> None:
    async with storage.unit_of_work() as uow:
        for number in numbers:
            await uow.plans.add_version(_version(review, number))
        await uow.commit()


class TestConversation:
    async def test_a_conversation_is_stored_and_read_back(self, storage):
        user, review = await _stored_review(storage)
        conversation = _conversation(review, user)

        async with storage.unit_of_work() as uow:
            await uow.plans.add_conversation(conversation)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.plans.get_conversation(review.id)

        assert stored == conversation

    async def test_a_review_has_one_conversation(self, storage):
        user, review = await _stored_review(storage)
        async with storage.unit_of_work() as uow:
            await uow.plans.add_conversation(_conversation(review, user))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.plans.add_conversation(_conversation(review, user))

    async def test_a_stale_copy_cannot_overwrite_a_newer_one(self, storage):
        user, review = await _stored_review(storage)
        conversation = _conversation(review, user)
        async with storage.unit_of_work() as uow:
            await uow.plans.add_conversation(conversation)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            saved = await uow.plans.save_conversation(
                replace(conversation, body_sealed=b"newer")
            )
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.plans.save_conversation(conversation)
            stored = await uow.plans.get_conversation(review.id)

        assert saved.version == 1
        assert stored == saved
        assert stored.body_sealed == b"newer"

    async def test_a_review_without_a_conversation_has_none(self, storage):
        _, review = await _stored_review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.plans.get_conversation(review.id) is None


class TestVersions:
    async def test_versions_are_listed_oldest_first_and_the_latest_is_found(self, storage):
        _, review = await _stored_review(storage)
        await _add_versions(storage, review, 1, 2, 3)

        async with storage.unit_of_work() as uow:
            listed = await uow.plans.list_versions(review.id)
            latest = await uow.plans.latest_version(review.id)
            second = await uow.plans.get_version(review.id, 2)
            missing = await uow.plans.get_version(review.id, 9)

        assert [v.version for v in listed] == [1, 2, 3]
        assert latest == _version(review, 3)
        assert second == _version(review, 2)
        assert missing is None

    async def test_a_review_without_versions_has_no_latest(self, storage):
        _, review = await _stored_review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.plans.latest_version(review.id) is None
            assert await uow.plans.latest_accepted(review.id) is None
            assert await uow.plans.list_versions(review.id) == []

    async def test_a_version_number_is_written_once(self, storage):
        """Two drafts of "the next version" cannot both be stored."""
        _, review = await _stored_review(storage)
        await _add_versions(storage, review, 1)

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.plans.add_version(_version(review, 1))

    async def test_the_author_and_origin_are_stored_in_the_clear(self, storage):
        user, review = await _stored_review(storage)
        uploaded = _version(
            review,
            1,
            author=PlanAuthor.person(user.id),
            origin=VocabularyOrigin.USER_SUPPLIED,
        )

        async with storage.unit_of_work() as uow:
            await uow.plans.add_version(uploaded)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.plans.get_version(review.id, 1)

        assert stored == uploaded


class TestAccept:
    async def test_the_first_accept_wins_and_a_second_learns_it_lost(self, storage):
        user, review = await _stored_review(storage)
        await _add_versions(storage, review, 1)

        async with storage.unit_of_work() as uow:
            first = await uow.plans.accept(review.id, 1, user.id, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            second = await uow.plans.accept(
                review.id, 1, uuid.uuid4(), NOW + timedelta(hours=1)
            )
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.plans.get_version(review.id, 1)

        assert (first, second) == (True, False)
        assert stored is not None
        assert (stored.accepted_at, stored.accepted_by) == (NOW, user.id)

    async def test_only_the_latest_version_can_be_accepted(self, storage):
        user, review = await _stored_review(storage)
        await _add_versions(storage, review, 1, 2)

        async with storage.unit_of_work() as uow:
            accepted = await uow.plans.accept(review.id, 1, user.id, NOW)
            await uow.commit()

        assert accepted is False

    async def test_a_missing_version_is_not_accepted(self, storage):
        user, review = await _stored_review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.plans.accept(review.id, 1, user.id, NOW) is False

    async def test_the_accepted_version_stays_in_force_under_a_newer_draft(self, storage):
        user, review = await _stored_review(storage)
        await _add_versions(storage, review, 1)
        async with storage.unit_of_work() as uow:
            await uow.plans.accept(review.id, 1, user.id, NOW)
            await uow.commit()
        await _add_versions(storage, review, 2)

        async with storage.unit_of_work() as uow:
            accepted = await uow.plans.latest_accepted(review.id)
            latest = await uow.plans.latest_version(review.id)

        assert accepted is not None
        assert accepted.version == 1
        assert latest is not None
        assert (latest.version, latest.is_accepted) == (2, False)

    async def test_an_uncommitted_accept_is_not_kept(self, storage):
        user, review = await _stored_review(storage)
        await _add_versions(storage, review, 1)

        async with storage.unit_of_work() as uow:
            await uow.plans.accept(review.id, 1, user.id, NOW)
        async with storage.unit_of_work() as uow:
            stored = await uow.plans.get_version(review.id, 1)

        assert stored is not None
        assert not stored.is_accepted


class TestReceipts:
    async def test_receipts_are_listed_oldest_first(self, storage):
        user, review = await _stored_review(storage)
        later = _receipt(review, at=NOW + timedelta(minutes=5), user=user)
        earlier = _receipt(review, at=NOW, user=None)

        async with storage.unit_of_work() as uow:
            await uow.plans.add_receipt(later)
            await uow.plans.add_receipt(earlier)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            listed = await uow.plans.list_receipts(review.id)

        assert listed == [earlier, later]

    async def test_a_receipt_keeps_the_version_and_change_request_it_belongs_to(self, storage):
        user, review = await _stored_review(storage)
        receipt = replace(
            _receipt(review, at=NOW, user=user), plan_version=2, change_request_id=uuid.uuid4()
        )

        async with storage.unit_of_work() as uow:
            await uow.plans.add_receipt(receipt)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            assert await uow.plans.list_receipts(review.id) == [receipt]


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresUnderConcurrency:
    async def test_concurrent_accepts_of_one_version_have_exactly_one_winner(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            user, review = await _stored_review(postgres)
            await _add_versions(postgres, review, 1)

            async def accept() -> bool:
                async with postgres.unit_of_work() as uow:
                    won = await uow.plans.accept(review.id, 1, user.id, NOW)
                    await asyncio.sleep(0.05)
                    await uow.commit()
                    return won

            outcomes = await asyncio.gather(accept(), accept())
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert sorted(outcomes) == [False, True]

    async def test_concurrent_writers_of_the_next_version_have_exactly_one_winner(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            _, review = await _stored_review(postgres)

            async def add() -> bool:
                try:
                    async with postgres.unit_of_work() as uow:
                        await uow.plans.add_version(_version(review, 1))
                        await asyncio.sleep(0.05)
                        await uow.commit()
                except ConcurrentUpdateError:
                    return False
                return True

            outcomes = await asyncio.gather(add(), add())
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert sorted(outcomes) == [False, True]


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresSchema:
    async def test_the_database_refuses_to_rewrite_an_accepted_version(self):
        """Defence in depth: even a bug in the app cannot unfreeze an accepted plan."""
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        engine = create_async_engine(_DATABASE_URL)
        try:
            user, review = await _stored_review(postgres)
            await _add_versions(postgres, review, 1)
            async with postgres.unit_of_work() as uow:
                await uow.plans.accept(review.id, 1, user.id, NOW)
                await uow.commit()

            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="written once"):
                    await connection.execute(
                        text("UPDATE plan_versions SET body_sealed = 'x' WHERE review_id = :r"),
                        {"r": review.id},
                    )
        finally:
            await engine.dispose()
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

    async def test_the_database_refuses_a_strict_vocabulary_written_by_the_model(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        engine = create_async_engine(_DATABASE_URL)
        try:
            _, review = await _stored_review(postgres)

            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="strict_vocabulary_written_by_a_person"):
                    await connection.execute(
                        text(
                            "INSERT INTO plan_versions (review_id, version, project_id, origin,"
                            " author_kind, created_at, body_sealed) VALUES (:r, 1, :p,"
                            " 'user_supplied', 'model', now(), 'x')"
                        ),
                        {"r": review.id, "p": review.project_id},
                    )
        finally:
            await engine.dispose()
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)
