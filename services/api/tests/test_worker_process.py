"""Requirements for the worker process's health signal.

Compose checks a container by running a command inside it. The worker container
is read-only with a tmpfs at /tmp, so the worker touches a heartbeat file there
every time it polls, wakes or renews a lease, and `python -m app.worker
--check-health` succeeds only while that file is fresh: younger than three poll
intervals, so one slow turn is tolerated and a stalled loop is not.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.runs import RunState
from app.worker import HEALTHY_WITHIN, Heartbeat, is_healthy, main
from tests.run_support import HookedStage, make_run_team


@pytest.fixture
def heartbeat_file(tmp_path: Path) -> Path:
    return tmp_path / "worker-heartbeat"


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


class TestHealthCheck:
    def test_a_fresh_heartbeat_is_healthy(self, heartbeat_file):
        Heartbeat(heartbeat_file).beat()

        assert is_healthy(heartbeat_file)
        assert main(["--check-health", "--heartbeat-file", str(heartbeat_file)]) == 0

    def test_a_heartbeat_older_than_three_poll_intervals_is_unhealthy(self, heartbeat_file):
        Heartbeat(heartbeat_file).beat()
        _age(heartbeat_file, HEALTHY_WITHIN.total_seconds() + 1)

        assert not is_healthy(heartbeat_file)
        assert main(["--check-health", "--heartbeat-file", str(heartbeat_file)]) == 1

    def test_a_heartbeat_just_inside_the_window_is_healthy(self, heartbeat_file):
        Heartbeat(heartbeat_file).beat()
        _age(heartbeat_file, HEALTHY_WITHIN.total_seconds() - 2)

        assert is_healthy(heartbeat_file)

    def test_a_worker_that_never_beat_is_unhealthy(self, heartbeat_file):
        assert not is_healthy(heartbeat_file)
        assert main(["--check-health", "--heartbeat-file", str(heartbeat_file)]) == 1

    def test_beating_again_makes_a_stale_heartbeat_fresh(self, heartbeat_file):
        heartbeat = Heartbeat(heartbeat_file)
        heartbeat.beat()
        _age(heartbeat_file, 3600)

        heartbeat.beat()

        assert is_healthy(heartbeat_file)


class TestTheWorkerBeats:
    def test_an_idle_worker_beats_on_every_turn(self):
        rt = make_run_team()
        beats: list[float] = []

        async def serve_three_turns() -> None:
            stop = asyncio.Event()

            def on_alive() -> None:
                beats.append(time.monotonic())
                if len(beats) == 3:
                    stop.set()

            worker = rt.worker({}, poll_seconds=0.01)
            await asyncio.wait_for(worker.serve(stop, on_alive=on_alive), timeout=5)

        asyncio.run(serve_three_turns())

        assert len(beats) == 3

    def test_a_busy_worker_beats_while_a_long_stage_runs(self):
        """A stage may take minutes; the worker must not look dead meanwhile."""
        rt = make_run_team()
        run_id = rt.start_run(["queries"])
        beats_during_stage: list[int] = []
        beats: list[int] = []

        async def slow(done: int) -> None:
            before = len(beats)
            await asyncio.sleep(0.2)
            beats_during_stage.append(len(beats) - before)

        async def run_one_slow_stage() -> None:
            stop = asyncio.Event()

            def on_alive() -> None:
                beats.append(1)
                if rt_run_finished():
                    stop.set()

            worker = rt.worker({"queries": HookedStage(slow, items=1)}, poll_seconds=0.02)
            await asyncio.wait_for(worker.serve(stop, on_alive=on_alive), timeout=5)

        def rt_run_finished() -> bool:
            return rt.harness.storage.state.runs[UUID(run_id)].state is RunState.DONE

        asyncio.run(run_one_slow_stage())

        assert beats_during_stage[0] >= 3
