"""Helpers for the run specs: a team with a review, workers with scripted stages, and the log.

Built on `tests/project_support.py` and as real as it is: requests go through
the real middleware, routes, storage, encryption and audit log, and the worker
is the real `RunWorker`. Only the stages are scripted, because the real ones
(discover, mask, screen, brief) belong to other phases.

Two instruments, neither of which changes behaviour:

- `ClockAdvancingSignals` makes every wait return at once and moves the fake
  clock by the whole timeout, so a quiet stream's minutes pass in microseconds;
- it also records how many transactions were open at each wait, so a test can
  prove a stream never holds a database connection while it waits.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response

from app.application.accounts import Current
from app.application.run_worker import RunWorker
from app.application.runs import RunService
from app.domain.encryption import EncryptionContext
from app.domain.runs import JsonValue, Run
from app.runs.base import Stage, StageContext, StageFailedError
from app.runs.fake import FakeStage
from tests.project_support import PROJECTS, Team, make_team, send
from tests.support import AuthHarness, FakeClock, TrackingStorage

# Realistic and sensitive on purpose: a question names a population, a place
# and a crisis. It must never appear in an event, a log or an audit row.
QUESTION = "Do cash transfers reduce acute malnutrition among displaced children in Marib?"

EVENT_PURPOSE = "run_events.payload"
LEASE = timedelta(seconds=60)


class ClockAdvancingSignals:
    """A `Signals` whose waits return at once, after moving the fake clock by the wait."""

    def __init__(self, clock: FakeClock, storage: TrackingStorage) -> None:
        self._clock = clock
        self._storage = storage
        self.published: list[str] = []
        self.open_units_at_wait: list[int] = []

    async def publish(self, channel: str) -> None:
        self.published.append(channel)

    async def wait(self, channel: str, seconds: float) -> bool:
        self.open_units_at_wait.append(self._storage.open_units)
        self._clock.advance(seconds=seconds)
        return False

    async def close(self) -> None:
        return None


class SimulatedCrashError(BaseException):
    """The worker process dying mid-stage: no handler in the worker may catch it.

    A `BaseException`, like `KeyboardInterrupt`, so that `except Exception` in
    the worker cannot turn a crash into a tidy failure the test did not ask for.
    """


class CrashingStage:
    """Kills its worker after reporting some progress, `times` times, then works."""

    def __init__(self, times: int = 1, items: int = 2) -> None:
        self.remaining = times
        self.items = items

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        await context.progress(1, self.items, "Item 1")
        if self.remaining > 0:
            self.remaining -= 1
            raise SimulatedCrashError
        for done in range(2, self.items + 1):
            await context.progress(done, self.items, f"Item {done}")
        return {"items": self.items}


class FailingStage:
    """Fails the way a broken dependency would, with text no user should see."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError(
            'Traceback (most recent call last): KeyError: "secret" at 10.0.0.7'
        )

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        await context.progress(1, 3, "Item 1")
        raise self.error


class HookedStage:
    """Calls `hook(n)` after item `n`, checking for cancellation before each item."""

    def __init__(self, hook: Callable[[int], Any], items: int = 3) -> None:
        self.hook = hook
        self.items = items
        self.items_done = 0

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        for done in range(1, self.items + 1):
            await context.raise_if_cancelled()
            await context.progress(done, self.items, f"Item {done}")
            self.items_done = done
            result = self.hook(done)
            if asyncio.iscoroutine(result):
                await result
        return {"items": self.items}


async def current_of(rt: RunTeam, browser: TestClient) -> Current:
    """The signed-in caller behind a browser, for calling a use case directly."""
    token = browser.cookies.get("__Host-session")
    assert token is not None
    current = await rt.harness.providers.accounts.authenticate(token)
    assert current is not None
    return current


def failing_with_message(message: str) -> FailingStage:
    return FailingStage(StageFailedError(message))


def simple_stages(*stage_ids: str, items: int = 2) -> dict[str, Stage]:
    return {stage_id: FakeStage(items=items) for stage_id in stage_ids}


@dataclass
class Event:
    id: int
    name: str
    data: dict[str, Any]


@dataclass
class RunTeam:
    """A project team with one review, and everything needed to run it."""

    team: Team
    signals: ClockAdvancingSignals
    review_id: str
    workers: list[RunWorker] = field(default_factory=list)

    @property
    def harness(self) -> AuthHarness:
        return self.team.harness

    @property
    def clock(self) -> FakeClock:
        return self.team.harness.clock

    @property
    def owner(self) -> TestClient:
        return self.team.owner

    @property
    def reviewer(self) -> TestClient:
        return self.team.reviewer

    @property
    def viewer(self) -> TestClient:
        return self.team.viewer

    @property
    def outsider(self) -> TestClient:
        return self.team.outsider

    @property
    def project_id(self) -> str:
        return self.team.project_id

    @property
    def runs(self) -> RunService:
        return self.harness.providers.runs

    def worker(
        self,
        stages: Mapping[str, Stage],
        worker_id: str = "worker-a",
        *,
        poll_seconds: float | None = None,
    ) -> RunWorker:
        """A real worker over this team's storage. `poll_seconds` is for specs of `serve`."""
        providers = self.harness.providers
        poll = (
            {} if poll_seconds is None else {"poll_interval": timedelta(seconds=poll_seconds)}
        )
        worker = RunWorker(
            storage=providers.storage,
            encryption=providers.encryption,
            signals=self.signals,
            stages=stages,
            worker_id=worker_id,
            clock=self.clock,
            lease=LEASE,
            **poll,
        )
        self.workers.append(worker)
        return worker

    def start_run(
        self, stages: Sequence[str] = ("queries", "discover"), browser: TestClient | None = None
    ) -> str:
        response = send(
            browser or self.owner,
            "POST",
            f"/api/v1/reviews/{self.review_id}/runs",
            {"stages": list(stages)},
        )
        assert response.status_code == 202, response.text
        run_id: str = response.json()["run_id"]
        return run_id

    def stored_run(self, run_id: str) -> Run:
        async def read() -> Run | None:
            async with self.harness.providers.storage.unit_of_work() as uow:
                return await uow.runs.get(UUID(run_id))

        run = asyncio.run(read())
        assert run is not None
        return run

    def events(self, run_id: str) -> list[Event]:
        """The run's event log, decrypted, exactly as storage holds it."""
        return asyncio.run(self.read_events(run_id))

    async def read_events(self, run_id: str) -> list[Event]:
        providers = self.harness.providers
        async with providers.storage.unit_of_work() as uow:
            stored = await uow.run_events.list_after(UUID(run_id), 0, limit=10_000)
            events = []
            for event in stored:
                plaintext = await providers.encryption.decrypt(
                    uow, event_context(self.project_id, run_id, event.id), event.payload_sealed
                )
                events.append(Event(event.id, event.name.value, json.loads(plaintext)))
        return events

    def stream(
        self,
        run_id: str,
        browser: TestClient | None = None,
        last_event_id: str | None = None,
        query: str = "",
    ) -> Response:
        headers = {} if last_event_id is None else {"Last-Event-ID": last_event_id}
        return (browser or self.owner).get(
            f"/api/v1/runs/{run_id}/events{query}", headers=headers
        )

    def gate_id(self, run_id: str) -> str:
        required = [e for e in self.events(run_id) if e.name == "gate.required"]
        assert required, "the run has not asked for a gate"
        gate_id: str = required[-1].data["gate_id"]
        return gate_id

    def resolve(
        self, gate_id: str, decision: str = "approve", browser: TestClient | None = None
    ) -> Response:
        return send(
            browser or self.owner, "POST", f"/api/v1/gates/{gate_id}", {"decision": decision}
        )

    def cancel(self, run_id: str, browser: TestClient | None = None) -> Response:
        return send(browser or self.owner, "POST", f"/api/v1/runs/{run_id}/cancel")


def event_context(project_id: str, run_id: str, event_id: int) -> EncryptionContext:
    """How an event's payload is bound: table "run_events", the run, and the event's id."""
    return EncryptionContext(UUID(project_id), EVENT_PURPOSE, f"{run_id}:{event_id}")


def reviews_path(project_id: str, suffix: str = "") -> str:
    return f"{PROJECTS}/{project_id}/reviews{suffix}"


def create_review(
    browser: TestClient, project_id: str, question: str = QUESTION
) -> dict[str, Any]:
    response = send(browser, "POST", reviews_path(project_id), {"question": question})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def use_test_signals(harness: AuthHarness) -> ClockAdvancingSignals:
    """Rebuild the run service around signals that never sleep. Nothing else changes."""
    providers = harness.providers
    signals = ClockAdvancingSignals(harness.clock, harness.storage)
    runs = RunService(
        storage=providers.storage,
        encryption=providers.encryption,
        audit=providers.audit,
        signals=signals,
        available_stages=tuple(providers.stages),
        session_policy=providers.accounts.session_policy,
        clock=harness.clock,
        poll_interval=timedelta(seconds=15),
    )
    harness.providers = dataclasses.replace(providers, runs=runs, signals=signals)
    harness.app.state.providers = harness.providers
    return signals


def make_run_team() -> RunTeam:
    team = make_team()
    signals = use_test_signals(team.harness)
    review = create_review(team.owner, team.project_id)
    return RunTeam(team, signals, review["id"])


def run_until_idle(*workers: RunWorker, limit: int = 20) -> int:
    """Let the workers take turns until none of them finds work. Returns how many turns ran."""

    async def drain() -> int:
        turns = 0
        while turns < limit:
            worked = [await worker.run_once() for worker in workers]
            if not any(worked):
                return turns
            turns += 1
        raise AssertionError("the workers never ran out of work")

    return asyncio.run(drain())


def parse_sse(body: str) -> tuple[list[str], list[Event], list[str]]:
    """Split an event-stream body into its `retry:` values, its events and its comments."""
    retries: list[str] = []
    events: list[Event] = []
    comments: list[str] = []
    for block in body.split("\n\n"):
        if not block:
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line[1:].strip())
                continue
            name, _, value = line.partition(":")
            fields[name] = value.removeprefix(" ")
        if "retry" in fields:
            retries.append(fields["retry"])
        if "event" in fields:
            events.append(Event(int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return retries, events, comments


def names(events: Sequence[Event]) -> list[str]:
    return [e.name for e in events]
