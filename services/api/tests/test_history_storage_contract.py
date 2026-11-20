"""One behaviour contract for a person's marks and dismissals, run against every backend.

Marks and dismissals are inserted once and never rewritten: marking again keeps the first
time, and of two tabs dismissing at once, one stores it. The Postgres run is skipped unless
TEST_DATABASE_URL points at a database migrated to revision 0019, as the runtime role.
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
from app.domain.discovery import StoredPaper
from app.domain.history import Dismissal
from app.domain.marks import BriefRead, PaperMark, PaperMarkKind
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


class _World:
    """A committed user with a project, a review, a run, a paper and a brief to mark."""

    def __init__(self) -> None:
        self.user = User(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex[:12]}@example.org",
            display_name="Test",
            password_hash="fake$x",
            created_at=NOW,
        )
        self.project = Project(
            id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=self.user.id
        )
        self.review = Review(
            id=uuid.uuid4(),
            project_id=self.project.id,
            question_sealed=b"sealed",
            status=ReviewStatus.DONE,
            created_at=NOW,
            created_by=self.user.id,
        )
        self.run = Run(
            id=uuid.uuid4(),
            review_id=self.review.id,
            project_id=self.project.id,
            stages=("answer",),
            state=RunState.QUEUED,
            created_at=NOW,
            created_by=self.user.id,
            queued_at=NOW,
        )
        self.paper = StoredPaper(
            id=uuid.uuid4(),
            project_id=self.project.id,
            review_id=self.review.id,
            first_run_id=self.run.id,
            identity_digest=b"digest",
            source_id="openalex",
            record_sealed=b"sealed",
            created_at=NOW,
        )
        self.brief = BriefRecord(
            id=uuid.uuid4(),
            project_id=self.project.id,
            review_id=self.review.id,
            run_id=self.run.id,
            version=1,
            created_at=NOW,
            model_id="qwen3.5:9b",
            rounds=1,
            kept_round=1,
            max_rounds=3,
            stopped_because=StopReason.ACCEPTED,
            content_sealed=b"sealed",
            evidence_sealed=b"sealed",
            trace_sealed=b"sealed",
        )

    async def store(self, storage: StorageProvider) -> _World:
        async with storage.unit_of_work() as uow:
            await uow.users.add(self.user)
            await uow.projects.add(self.project)
            await uow.reviews.add(self.review)
            await uow.runs.add(self.run)
            await uow.papers.add(self.paper)
            assert await uow.briefs.add(self.brief)
            await uow.commit()
        return self

    def mark(self, kind: PaperMarkKind = PaperMarkKind.SEEN, minutes: int = 0) -> PaperMark:
        return PaperMark(
            user_id=self.user.id,
            paper_id=self.paper.id,
            kind=kind,
            project_id=self.project.id,
            identity_sealed=f"sealed-{kind.value}".encode(),
            marked_at=NOW + timedelta(minutes=minutes),
        )


class TestPaperMarks:
    async def test_a_mark_is_read_back_with_the_person_s_marks(self, storage):
        world = await _World().store(storage)
        mark = world.mark()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.add_paper_mark(mark)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.list_paper_marks(world.user.id) == [mark]
            assert await uow.marks.list_paper_marks(uuid.uuid4()) == []

    async def test_marking_again_keeps_the_first_time(self, storage):
        world = await _World().store(storage)
        first = world.mark()
        async with storage.unit_of_work() as uow:
            await uow.marks.add_paper_mark(first)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert not await uow.marks.add_paper_mark(world.mark(minutes=5))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.list_paper_marks(world.user.id) == [first]

    async def test_seen_and_saved_are_separate_marks_and_saved_can_be_removed(self, storage):
        world = await _World().store(storage)
        seen, saved = world.mark(), world.mark(PaperMarkKind.SAVED, minutes=1)
        async with storage.unit_of_work() as uow:
            await uow.marks.add_paper_mark(seen)
            await uow.marks.add_paper_mark(saved)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.remove_paper_mark(
                world.user.id, world.paper.id, PaperMarkKind.SAVED
            )
            assert not await uow.marks.remove_paper_mark(
                world.user.id, world.paper.id, PaperMarkKind.SAVED
            )
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.list_paper_marks(world.user.id) == [seen]

    async def test_an_uncommitted_mark_is_not_stored(self, storage):
        world = await _World().store(storage)

        async with storage.unit_of_work() as uow:
            await uow.marks.add_paper_mark(world.mark())

        async with storage.unit_of_work() as uow:
            assert await uow.marks.list_paper_marks(world.user.id) == []


class TestBriefReads:
    async def test_a_read_is_stored_once(self, storage):
        world = await _World().store(storage)
        read = BriefRead(world.user.id, world.brief.id, world.project.id, NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.marks.add_brief_read(read)
            assert not await uow.marks.add_brief_read(replace(read, read_at=NOW + timedelta(1)))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.marks.list_brief_reads(world.user.id) == [read]


class TestDismissals:
    async def test_a_dismissal_is_stored_once_per_person_and_key(self, storage):
        world = await _World().store(storage)
        dismissal = Dismissal(world.user.id, f"failed_run:{world.run.id}", NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.dismissals.add(dismissal)
            assert not await uow.dismissals.add(replace(dismissal, dismissed_at=NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.dismissals.list_for_user(world.user.id) == [dismissal]
            assert await uow.dismissals.list_for_user(uuid.uuid4()) == []
