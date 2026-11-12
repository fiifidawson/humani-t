"""Requirements for the worker and the live stream, below HTTP.

The test client buffers a whole response, so these drive `RunService.open_stream`
directly, with a clock that moves only when a wait times out: a quiet hour passes in
microseconds and nothing sleeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest

from app.application.runs import KeepAlive, StreamEvent, StreamItem
from app.domain.accounts import NotFoundError
from app.domain.runs import MAX_RECOVERIES, GateDecision, GateKind, RunState
from app.runs.fake import FakeStage
from tests.project_support import PROJECTS, create_project, send
from tests.run_support import (
    LEASE,
    CrashingStage,
    HookedStage,
    RunTeam,
    SimulatedCrashError,
    create_review,
    current_of,
    make_run_team,
    names,
    run_until_idle,
    simple_stages,
)


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def _gated() -> dict[str, FakeStage]:
    return {
        "queries": FakeStage(items=1),
        "discover": FakeStage(items=2, gate=GateKind.VOCABULARY),
    }


async def _take(stream: AsyncIterator[StreamItem], count: int) -> list[StreamItem]:
    taken = []
    async for item in stream:
        taken.append(item)
        if len(taken) == count:
            break
    return taken


async def _drain(stream: AsyncIterator[StreamItem], limit: int = 10_000) -> list[StreamItem]:
    """Read until the stream ends by itself; `limit` fails a stream that never ends."""
    items = []
    async for item in stream:
        items.append(item)
        assert len(items) < limit, "the stream never ended"
    return items


def _event_names(items: list[StreamItem]) -> list[str]:
    return [i.name for i in items if isinstance(i, StreamEvent)]


class TestExactlyOneFinish:
    """`job.finished` is sent exactly once and is always a run's last event."""

    @pytest.mark.parametrize("outcome", ["done", "failed", "cancelled"])
    def test_every_way_a_run_can_end_writes_one_job_finished_last(self, rt, outcome):
        run_id = rt.start_run(["queries", "discover"])
        stages = simple_stages("queries", "discover")
        if outcome == "failed":
            stages["discover"] = CrashingStage(times=MAX_RECOVERIES + 1)
        if outcome == "cancelled":
            stages["discover"] = HookedStage(lambda done: rt.cancel(run_id))
        worker = rt.worker(stages)

        for _ in range(MAX_RECOVERIES + 2):
            try:
                run_until_idle(worker)
            except SimulatedCrashError:
                rt.clock.advance(seconds=LEASE.total_seconds() + 1)

        events = rt.events(run_id)
        assert names(events).count("job.finished") == 1
        assert events[-1].name == "job.finished"
        assert events[-1].data["outcome"] == outcome

    def test_nothing_can_be_added_to_the_log_after_job_finished(self, rt):
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        async def append_late() -> int | None:
            async with rt.harness.providers.storage.unit_of_work() as uow:
                return await uow.run_events.next_id(UUID(run_id))

        assert asyncio.run(append_late()) is None

    def test_event_ids_have_no_gaps_across_a_gate_and_a_crash(self, rt):
        run_id = rt.start_run(["queries", "discover", "screen"])
        stages = _gated() | {"screen": CrashingStage(times=1)}
        worker = rt.worker(stages)
        run_until_idle(worker)
        rt.resolve(rt.gate_id(run_id))
        with pytest.raises(SimulatedCrashError):
            run_until_idle(worker)
        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker(stages, "worker-b"))

        ids = [e.id for e in rt.events(run_id)]
        assert ids == list(range(1, len(ids) + 1))


class TestCrashRecovery:
    """A worker that dies mid-stage leaves a lease that expires; the run never sticks."""

    def test_an_expired_lease_is_resumed_by_another_worker_from_the_stage_it_was_in(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        stages = simple_stages("queries") | {"discover": CrashingStage(times=1)}
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker(stages, "worker-a"))
        assert rt.stored_run(run_id).state is RunState.RUNNING

        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker(stages, "worker-b"))

        events = rt.events(run_id)
        assert rt.stored_run(run_id).state is RunState.DONE
        finished = [e.data["stage"] for e in events if e.name == "stage.finished"]
        assert finished == ["queries", "discover"]
        started = [e.data["stage"] for e in events if e.name == "stage.started"]
        assert started == ["queries", "discover"]

    def test_a_lease_that_has_not_expired_is_left_alone(self, rt):
        run_id = rt.start_run(["queries"])
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker({"queries": CrashingStage(times=1)}, "worker-a"))

        rt.clock.advance(seconds=LEASE.total_seconds())
        turns = run_until_idle(rt.worker(simple_stages("queries"), "worker-b"))

        assert turns == 0
        assert rt.stored_run(run_id).lease_owner == "worker-a"

    def test_a_run_that_keeps_crashing_is_failed_in_plain_language(self, rt):
        run_id = rt.start_run(["queries"])
        stages = {"queries": CrashingStage(times=100)}

        for attempt in range(MAX_RECOVERIES + 1):
            with pytest.raises(SimulatedCrashError):
                run_until_idle(rt.worker(stages, f"worker-{attempt}"))
            rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker(stages, "worker-last"))

        run = rt.stored_run(run_id)
        finished = rt.events(run_id)[-1]
        assert run.state is RunState.FAILED
        assert finished.name == "job.finished"
        assert finished.data["outcome"] == "failed"
        assert "stopped unexpectedly" in finished.data["error"]

    def test_a_crashed_run_someone_asked_to_cancel_ends_cancelled(self, rt):
        run_id = rt.start_run(["queries"])
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker({"queries": CrashingStage(times=1)}))
        rt.cancel(run_id)

        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker(simple_stages("queries"), "worker-b"))

        assert rt.events(run_id)[-1].data["outcome"] == "cancelled"

    def test_a_worker_that_lost_its_lease_cannot_write_to_the_run_any_more(self, rt):
        """A stalled worker that wakes up after its lease was taken over must stay silent."""
        run_id = rt.start_run(["queries"])
        late_writes: list[BaseException] = []

        async def stall_then_write(done: int) -> None:
            rt.clock.advance(seconds=LEASE.total_seconds() + 1)
            await rt.worker(simple_stages("queries"), "worker-b").run_once()

        stalled = rt.worker({"queries": HookedStage(stall_then_write, items=2)}, "worker-a")
        try:
            asyncio.run(stalled.run_once())
        except BaseException as exc:
            late_writes.append(exc)

        events = rt.events(run_id)
        assert late_writes == []
        assert names(events).count("job.finished") == 1
        assert rt.stored_run(run_id).state is RunState.DONE

    def test_a_worker_shut_down_mid_stage_hands_the_run_back_at_once(self, rt):
        """A deploy is not a crash: the run is queued again without waiting for the lease."""
        run_id = rt.start_run(["queries"])
        started = asyncio.Event()
        never = asyncio.Event()

        async def block(done: int) -> None:
            started.set()
            await never.wait()

        async def shut_down_mid_stage() -> None:
            worker = rt.worker({"queries": HookedStage(block)})
            task = asyncio.create_task(worker.run_once())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(shut_down_mid_stage())

        run = rt.stored_run(run_id)
        assert run.state is RunState.QUEUED
        assert run.recoveries == 0
        run_until_idle(rt.worker(simple_stages("queries"), "worker-b"))
        assert rt.stored_run(run_id).state is RunState.DONE


class TestGatesAtTheWorker:
    def test_waiting_holds_no_lease_and_no_worker(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())

        run_until_idle(worker)
        rt.clock.advance(hours=24)

        assert run_until_idle(worker) == 0
        run = rt.stored_run(run_id)
        assert (run.state, run.lease_owner, run.lease_expires_at) == (
            RunState.WAITING,
            None,
            None,
        )

    def test_a_rejection_reaches_the_stage_which_decides_what_follows(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())
        run_until_idle(worker)

        rt.resolve(rt.gate_id(run_id), "reject")
        run_until_idle(worker)

        summary = [e for e in rt.events(run_id) if e.name == "stage.finished"][-1].data[
            "summary"
        ]
        assert summary["decision"] == "reject"

    def test_resolving_wakes_the_workers(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        rt.signals.published.clear()

        rt.resolve(rt.gate_id(run_id))

        assert "runs:queued" in rt.signals.published
        assert f"run:{run_id}" in rt.signals.published


class TestAgentSteps:
    def test_a_stage_reports_each_agent_step_as_one_event(self, rt):
        run_id = rt.start_run(["answer"])

        run_until_idle(rt.worker({"answer": FakeStage(items=0, agent_rounds=1)}))

        steps = [e.data for e in rt.events(run_id) if e.name == "agent.step"]
        assert [(s["round"], s["role"], s["status"]) for s in steps] == [
            (0, "planner", "ok"),
            (1, "retriever", "ok"),
            (1, "writer", "ok"),
            (1, "critic", "ok"),
        ]


class TestTheLiveStream:
    def test_a_stream_replays_then_follows_the_run_as_it_happens(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())
        run_until_idle(worker)

        async def follow() -> list[StreamItem]:
            current = await current_of(rt, rt.viewer)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            replayed = await _take(stream, len(await rt.read_events(run_id)))
            last = replayed[-1]
            assert isinstance(last, StreamEvent)
            assert last.name == "gate.required"
            gate_id = UUID(str(last.data["gate_id"]))
            owner = await current_of(rt, rt.owner)
            await rt.runs.resolve_gate(owner, gate_id, GateDecision.APPROVE, None)
            await worker.run_once()
            return replayed + await _drain(stream)

        items = asyncio.run(follow())

        assert _event_names(items)[-1] == "job.finished"
        ids = [i.id for i in items if isinstance(i, StreamEvent)]
        assert ids == list(range(1, len(ids) + 1))

    def test_every_open_stream_sees_a_gate_resolution(self, rt):
        """Other tabs and other members see the decision."""
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        replay = len(rt.events(run_id))
        gate_id = UUID(rt.gate_id(run_id))

        async def two_tabs() -> tuple[list[StreamItem], list[StreamItem]]:
            viewer = await current_of(rt, rt.viewer)
            reviewer = await current_of(rt, rt.reviewer)
            first = await rt.runs.open_stream(viewer, UUID(run_id), None)
            second = await rt.runs.open_stream(reviewer, UUID(run_id), str(replay))
            await _take(first, replay)
            await rt.runs.resolve_gate(reviewer, gate_id, GateDecision.APPROVE, None)
            return await _take(first, 1), await _take(second, 1)

        first, second = asyncio.run(two_tabs())

        assert _event_names(first) == _event_names(second) == ["gate.resolved"]

    def test_a_quiet_stream_sends_a_keep_alive_every_15_seconds(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        replay = len(rt.events(run_id))

        async def listen() -> list[float]:
            current = await current_of(rt, rt.owner)
            stream = await rt.runs.open_stream(current, UUID(run_id), str(replay))
            start = rt.clock()
            seen = []
            async for item in stream:
                assert isinstance(item, KeepAlive)
                seen.append((rt.clock() - start).total_seconds())
                if len(seen) == 4:
                    break
            return seen

        assert asyncio.run(listen()) == [15.0, 30.0, 45.0, 60.0]

    def test_a_revoked_session_ends_the_stream_within_a_minute(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        async def revoke_while_watching() -> timedelta:
            current = await current_of(rt, rt.viewer)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            await _take(stream, len(await rt.read_events(run_id)))
            await rt.harness.providers.accounts.logout(current, None)
            revoked_at = rt.clock()
            await _drain(stream)
            return rt.clock() - revoked_at

        assert asyncio.run(revoke_while_watching()) <= timedelta(minutes=1)

    def test_an_expired_session_ends_the_stream(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        async def watch_for_hours() -> timedelta:
            current = await current_of(rt, rt.viewer)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            start = rt.clock()
            await _drain(stream)
            return rt.clock() - start

        elapsed = asyncio.run(watch_for_hours())

        policy = rt.harness.providers.accounts.session_policy
        assert policy.idle_timeout <= elapsed <= policy.idle_timeout + timedelta(minutes=1)

    def test_losing_membership_ends_the_stream_within_a_minute(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        async def removed_while_watching() -> timedelta:
            current = await current_of(rt, rt.viewer)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            await _take(stream, len(await rt.read_events(run_id)))
            async with rt.harness.providers.storage.unit_of_work() as uow:
                await uow.memberships.remove(UUID(rt.project_id), current.user.id)
                await uow.commit()
            removed_at = rt.clock()
            await _drain(stream)
            return rt.clock() - removed_at

        assert asyncio.run(removed_while_watching()) <= timedelta(minutes=1)

    def test_a_stream_never_holds_a_database_connection_while_it_waits(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        async def watch() -> None:
            current = await current_of(rt, rt.owner)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            await _take(stream, 20)

        asyncio.run(watch())

        assert rt.signals.open_units_at_wait
        assert set(rt.signals.open_units_at_wait) == {0}

    def test_a_disconnect_does_not_stop_the_run(self, rt):
        """The stream only reads the log; closing it reaches nothing the worker uses."""
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())
        run_until_idle(worker)

        async def watch_then_leave() -> None:
            current = await current_of(rt, rt.owner)
            stream = await rt.runs.open_stream(current, UUID(run_id), None)
            await _take(stream, 2)
            await stream.aclose()

        asyncio.run(watch_then_leave())
        rt.resolve(rt.gate_id(run_id))
        run_until_idle(worker)

        assert rt.stored_run(run_id).state is RunState.DONE

    def test_a_stream_opened_by_a_non_member_is_refused_before_it_starts(self, rt):
        run_id = rt.start_run(["queries"])

        async def open_as_outsider() -> None:
            current = await current_of(rt, rt.outsider)
            await rt.runs.open_stream(current, UUID(run_id), None)

        with pytest.raises(NotFoundError):
            asyncio.run(open_as_outsider())


class TestDeletedProjects:
    """Deleting a project shreds its key, so nothing about its runs can be written again."""

    def _other_run(self, rt: RunTeam) -> str:
        project_id = create_project(rt.owner, "Market assessment, Mopti")["id"]
        review_id = create_review(rt.owner, project_id)["id"]
        response = send(
            rt.owner, "POST", f"/api/v1/reviews/{review_id}/runs", {"stages": ["queries"]}
        )
        assert response.status_code == 202
        run_id: str = response.json()["run_id"]
        return run_id

    def test_a_queued_run_of_a_deleted_project_ends_without_blocking_the_queue(self, rt):
        doomed = rt.start_run(["queries"])
        other = self._other_run(rt)

        assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204
        run_until_idle(rt.worker(simple_stages("queries")))

        assert rt.stored_run(doomed).state is RunState.CANCELLED
        assert rt.stored_run(other).state is RunState.DONE

    def test_a_crashed_run_of_a_deleted_project_does_not_block_recovery(self, rt):
        doomed = rt.start_run(["queries"])
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker({"queries": CrashingStage(times=1)}))
        assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204
        other = self._other_run(rt)

        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker(simple_stages("queries"), "worker-b"))

        assert rt.stored_run(doomed).is_finished
        assert rt.stored_run(other).state is RunState.DONE

    def test_a_run_whose_project_is_deleted_mid_stage_stops_at_its_next_write(self, rt):
        doomed = rt.start_run(["queries"])

        def delete_project(done: int) -> None:
            assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204

        run_until_idle(rt.worker({"queries": HookedStage(delete_project, items=2)}))

        assert rt.stored_run(doomed).state is RunState.CANCELLED
