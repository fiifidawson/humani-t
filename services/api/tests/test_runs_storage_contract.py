"""One behaviour contract for review, run, event and gate storage, run against every backend.

The run table is the job queue: one worker claims a queued run and holds it under an
expiring lease. Event ids rise by one with no gaps, nothing follows a finished run, a gate
is resolved once, and stale copies cannot overwrite. Only the Postgres run truly races; it
is skipped unless TEST_DATABASE_URL points at a migrated database as the runtime role.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.accounts import User
from app.domain.errors import ConcurrentUpdateError
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus, ReviewTrigger
from app.domain.runs import (
    EventName,
    Gate,
    GateDecision,
    GateKind,
    Run,
    RunEvent,
    RunState,
    RunTrigger,
)
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)
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


async def _project(storage: StorageProvider) -> tuple[User, Project]:
    """A committed user and a project they created: the parents every row below needs."""
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
    return user, project


def _review(user: User, project: Project, *, at: datetime = NOW) -> Review:
    return Review(
        id=uuid.uuid4(),
        project_id=project.id,
        question_sealed=b"sealed-question",
        status=ReviewStatus.NEW,
        created_at=at,
        created_by=user.id,
    )


def _run(review: Review, *, at: datetime = NOW, state: RunState = RunState.QUEUED) -> Run:
    return Run(
        id=uuid.uuid4(),
        review_id=review.id,
        project_id=review.project_id,
        stages=("queries", "discover"),
        state=state,
        created_at=at,
        created_by=review.created_by,
        queued_at=at,
    )


async def _stored_review(storage: StorageProvider) -> Review:
    user, project = await _project(storage)
    review = _review(user, project)
    async with storage.unit_of_work() as uow:
        await uow.reviews.add(review)
        await uow.commit()
    return review


async def _stored_run(
    storage: StorageProvider,
    review: Review | None = None,
    *,
    at: datetime = NOW,
    state: RunState = RunState.QUEUED,
) -> Run:
    review = review or await _stored_review(storage)
    run = _run(review, at=at, state=state)
    async with storage.unit_of_work() as uow:
        await uow.runs.add(run)
        await uow.commit()
    return run


async def _claimed_run(storage: StorageProvider, worker: str = "worker-a") -> Run:
    await _stored_run(storage)
    async with storage.unit_of_work() as uow:
        claimed = await uow.runs.claim_next(worker, NOW, LEASE)
        await uow.commit()
    assert claimed is not None
    return claimed


class TestReviews:
    async def test_a_committed_review_is_visible_to_the_next_unit_of_work(self, storage):
        review = await _stored_review(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.reviews.get(review.id) == review

    async def test_uncommitted_work_is_discarded(self, storage):
        user, project = await _project(storage)
        review = _review(user, project)
        async with storage.unit_of_work() as uow:
            await uow.reviews.add(review)

        async with storage.unit_of_work() as uow:
            assert await uow.reviews.get(review.id) is None

    async def test_a_project_lists_only_its_own_reviews_newest_first(self, storage):
        user, project = await _project(storage)
        _, other = await _project(storage)
        older = _review(user, project)
        newer = _review(user, project, at=NOW + timedelta(minutes=1))
        async with storage.unit_of_work() as uow:
            for review in (older, newer, _review(user, other)):
                await uow.reviews.add(review)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            listed = await uow.reviews.list_for_project(project.id)

        assert [r.id for r in listed] == [newer.id, older.id]

    async def test_saving_returns_the_stored_copy_with_its_version_moved_on(self, storage):
        review = await _stored_review(storage)

        async with storage.unit_of_work() as uow:
            saved = await uow.reviews.save(review.transitioned(ReviewTrigger.START_RUN))
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.reviews.get(review.id)

        assert saved == stored
        assert stored is not None
        assert stored.status is ReviewStatus.RUNNING
        assert stored.version == review.version + 1

    async def test_a_stale_copy_cannot_overwrite_a_newer_one(self, storage):
        stale = await _stored_review(storage)
        async with storage.unit_of_work() as uow:
            await uow.reviews.save(stale.transitioned(ReviewTrigger.START_RUN))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.reviews.save(stale.transitioned(ReviewTrigger.DRAFT))


class TestRuns:
    async def test_a_committed_run_is_visible_and_locking_reads_the_same_run(self, storage):
        run = await _stored_run(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.runs.get(run.id) == run
            assert await uow.runs.lock(run.id) == run

    async def test_a_missing_run_is_none(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.runs.get(uuid.uuid4()) is None
            assert await uow.runs.lock(uuid.uuid4()) is None

    async def test_a_review_lists_its_runs_newest_first(self, storage):
        review = await _stored_review(storage)
        first = await _stored_run(storage, review)
        second = await _stored_run(storage, review, at=NOW + timedelta(minutes=1))
        await _stored_run(storage)

        async with storage.unit_of_work() as uow:
            listed = await uow.runs.list_for_review(review.id)

        assert [r.id for r in listed] == [second.id, first.id]

    async def test_saving_moves_the_version_on_and_a_stale_copy_is_refused(self, storage):
        run = await _stored_run(storage)
        async with storage.unit_of_work() as uow:
            saved = await uow.runs.save(run.finished(RunTrigger.CANCEL, NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.runs.save(run.claimed("worker-a", NOW, LEASE))
            stored = await uow.runs.get(run.id)

        assert saved == stored
        assert stored is not None
        assert stored.state is RunState.CANCELLED
        assert stored.version == run.version + 1

    async def test_every_field_survives_a_round_trip(self, storage):
        run = replace(
            (await _claimed_run(storage)).stage_started(NOW).with_cancel_requested(NOW),
            recoveries=1,
        )
        async with storage.unit_of_work() as uow:
            saved = await uow.runs.save(run)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.runs.get(run.id) == saved == replace(run, version=run.version + 1)


class TestTheQueue:
    async def test_the_longest_queued_run_is_claimed_first(self, storage):
        review = await _stored_review(storage)
        newer = await _stored_run(storage, review, at=NOW + timedelta(seconds=5))
        older = await _stored_run(storage, review)

        async with storage.unit_of_work() as uow:
            claimed = await uow.runs.claim_next("worker-a", NOW, LEASE)
            await uow.commit()

        assert claimed is not None
        assert claimed.id == older.id != newer.id

    async def test_a_claim_takes_a_lease_in_the_workers_name(self, storage):
        claimed = await _claimed_run(storage, "worker-a")

        async with storage.unit_of_work() as uow:
            stored = await uow.runs.get(claimed.id)

        assert stored == claimed
        assert stored.state is RunState.RUNNING
        assert stored.lease_owner == "worker-a"
        assert stored.lease_expires_at == NOW + LEASE

    async def test_a_claimed_run_is_not_claimed_again(self, storage):
        await _claimed_run(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.runs.claim_next("worker-b", NOW, LEASE) is None

    async def test_only_queued_runs_are_claimed(self, storage):
        review = await _stored_review(storage)
        for state in (RunState.WAITING, RunState.DONE, RunState.CANCELLED):
            await _stored_run(storage, review, state=state)

        async with storage.unit_of_work() as uow:
            assert await uow.runs.claim_next("worker-a", NOW, LEASE) is None

    async def test_the_lease_holder_renews_its_lease_and_no_one_else_can(self, storage):
        claimed = await _claimed_run(storage, "worker-a")
        later = NOW + 2 * LEASE

        async with storage.unit_of_work() as uow:
            by_owner = await uow.runs.renew_lease(claimed.id, "worker-a", later)
            by_other = await uow.runs.renew_lease(claimed.id, "worker-b", later)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.runs.get(claimed.id)

        assert (by_owner, by_other) == (True, False)
        assert stored is not None
        assert stored.lease_expires_at == later

    async def test_a_lease_cannot_be_renewed_once_the_run_stopped_running(self, storage):
        claimed = await _claimed_run(storage, "worker-a")
        async with storage.unit_of_work() as uow:
            await uow.runs.save(claimed.paused())
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert not await uow.runs.renew_lease(claimed.id, "worker-a", NOW + 2 * LEASE)

    async def test_only_running_runs_past_their_lease_are_found_expired(self, storage):
        expired = await _claimed_run(storage)
        review = await _stored_review(storage)
        await _stored_run(storage, review)

        async with storage.unit_of_work() as uow:
            at_the_end = await uow.runs.lock_expired(NOW + LEASE, limit=10)
            after_it = await uow.runs.lock_expired(NOW + LEASE + timedelta(seconds=1), limit=10)

        assert at_the_end == []
        assert [r.id for r in after_it] == [expired.id]


def _event(run_id: UUID, event_id: int, name: EventName = EventName.STAGE_PROGRESS) -> RunEvent:
    return RunEvent(
        run_id=run_id,
        id=event_id,
        name=name,
        payload_sealed=f"sealed-{event_id}".encode(),
        occurred_at=NOW,
    )


async def _append(storage: StorageProvider, run_id: UUID, count: int) -> list[RunEvent]:
    events = []
    for _ in range(count):
        async with storage.unit_of_work() as uow:
            event_id = await uow.run_events.next_id(run_id)
            assert event_id is not None
            event = _event(run_id, event_id)
            await uow.run_events.add(event)
            await uow.commit()
        events.append(event)
    return events


class TestEventLog:
    async def test_event_ids_start_at_one_and_increase_by_one_within_each_run(self, storage):
        first = await _stored_run(storage)
        second = await _stored_run(storage)

        a = await _append(storage, first.id, 3)
        b = await _append(storage, second.id, 2)

        assert [e.id for e in a] == [1, 2, 3]
        assert [e.id for e in b] == [1, 2]

    async def test_events_are_read_back_in_order_after_a_given_id(self, storage):
        run = await _stored_run(storage)
        events = await _append(storage, run.id, 5)

        async with storage.unit_of_work() as uow:
            everything = await uow.run_events.list_after(run.id, 0, limit=100)
            after_two = await uow.run_events.list_after(run.id, 2, limit=100)
            capped = await uow.run_events.list_after(run.id, 0, limit=2)
            last = await uow.run_events.last_id(run.id)

        assert everything == events
        assert after_two == events[2:]
        assert capped == events[:2]
        assert last == 5

    async def test_a_run_with_no_events_has_last_id_zero(self, storage):
        run = await _stored_run(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.run_events.last_id(run.id) == 0
            assert await uow.run_events.list_after(run.id, 0, limit=10) == []

    async def test_no_event_can_follow_a_finished_run(self, storage):
        run = await _stored_run(storage)
        async with storage.unit_of_work() as uow:
            await uow.runs.save(run.finished(RunTrigger.CANCEL, NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.run_events.next_id(run.id) is None

    async def test_a_missing_run_has_no_next_event(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.run_events.next_id(uuid.uuid4()) is None

    async def test_a_worker_may_add_events_only_while_it_holds_the_lease(self, storage):
        claimed = await _claimed_run(storage, "worker-a")

        async with storage.unit_of_work() as uow:
            holder = await uow.run_events.next_id(claimed.id, lease_owner="worker-a")
            other = await uow.run_events.next_id(claimed.id, lease_owner="worker-b")
        async with storage.unit_of_work() as uow:
            await uow.runs.save(claimed.paused())
            await uow.commit()
        async with storage.unit_of_work() as uow:
            after_pause = await uow.run_events.next_id(claimed.id, lease_owner="worker-a")

        assert (holder, other, after_pause) == (1, None, None)


def _open_gate(run: Run) -> Gate:
    return Gate(
        id=uuid.uuid4(),
        run_id=run.id,
        project_id=run.project_id,
        stage="mask",
        kind=GateKind.PRIVACY_REVIEW,
        payload_sealed=b"sealed-payload",
        created_at=NOW,
    )


async def _stored_gate(storage: StorageProvider) -> tuple[Gate, User]:
    run = await _stored_run(storage)
    gate = _open_gate(run)
    async with storage.unit_of_work() as uow:
        await uow.gates.add(gate)
        await uow.commit()
        user = await uow.users.get(run.created_by)
    assert user is not None
    return gate, user


class TestGates:
    async def test_a_gate_is_found_by_id_and_by_run_stage_and_kind(self, storage):
        gate, _ = await _stored_gate(storage)

        async with storage.unit_of_work() as uow:
            by_id = await uow.gates.get(gate.id)
            found = await uow.gates.find(gate.run_id, "mask", GateKind.PRIVACY_REVIEW)
            other_kind = await uow.gates.find(gate.run_id, "mask", GateKind.VOCABULARY)

        assert by_id == found == gate
        assert other_kind is None

    async def test_the_first_resolution_wins_and_the_second_learns_it_lost(self, storage):
        gate, user = await _stored_gate(storage)
        approve = gate.resolved(user.id, GateDecision.APPROVE, b"sealed-revisions", NOW)
        reject = gate.resolved(user.id, GateDecision.REJECT, None, NOW)

        async with storage.unit_of_work() as uow:
            first = await uow.gates.resolve(approve)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            second = await uow.gates.resolve(reject)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.gates.get(gate.id)

        assert (first, second) == (True, False)
        assert stored == approve


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresUnderConcurrency:
    async def test_two_workers_claiming_at_once_get_different_runs_without_waiting(self):
        """SKIP LOCKED: the second worker passes over the row the first is holding."""
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            review = await _stored_review(postgres)
            runs = {(await _stored_run(postgres, review)).id for _ in range(2)}

            async def claim(worker: str) -> UUID | None:
                async with postgres.unit_of_work() as uow:
                    claimed = await uow.runs.claim_next(worker, NOW, LEASE)
                    await asyncio.sleep(0.2)  # hold the row while the other claims
                    await uow.commit()
                    return None if claimed is None else claimed.id

            claimed = await asyncio.wait_for(
                asyncio.gather(claim("worker-a"), claim("worker-b")), timeout=2
            )
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert set(claimed) == runs

    async def test_concurrent_resolutions_of_one_gate_have_exactly_one_winner(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            gate, user = await _stored_gate(postgres)

            async def resolve(decision: GateDecision) -> bool:
                async with postgres.unit_of_work() as uow:
                    won = await uow.gates.resolve(gate.resolved(user.id, decision, None, NOW))
                    await asyncio.sleep(0.05)
                    await uow.commit()
                    return won

            outcomes = await asyncio.gather(
                resolve(GateDecision.APPROVE), resolve(GateDecision.REJECT)
            )
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert sorted(outcomes) == [False, True]

    async def test_concurrent_writers_never_repeat_or_skip_an_event_id(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            run = await _stored_run(postgres)

            async def append() -> int:
                async with postgres.unit_of_work() as uow:
                    event_id = await uow.run_events.next_id(run.id)
                    assert event_id is not None
                    await uow.run_events.add(_event(run.id, event_id))
                    await uow.commit()
                    return event_id

            ids = await asyncio.gather(*(append() for _ in range(20)))
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert sorted(ids) == list(range(1, 21))

    async def test_the_app_role_cannot_rewrite_a_stored_event(self):
        assert _DATABASE_URL is not None
        engine = create_async_engine(_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(text("UPDATE run_events SET name = 'x'"))
        finally:
            await engine.dispose()
