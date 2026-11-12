"""Runs, their event log and their gates, as rules with no I/O.

This module decides what may follow what: the run's states, where a crashed run goes and
which stages a run may ask for. Event payloads carry counts, labels and masked text only,
and are encrypted under the project's key before they are stored.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.projects import Action
from app.domain.reviews import InvalidTransitionError

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

# The pipeline, in run order. A run covers some of these, always in this order.
STAGES: tuple[str, ...] = (
    "queries",
    "discover",
    "ingest",
    "mask",
    "screen",
    "export",
    "index",
    "answer",
)

# Words a non-technical user knows, for error messages.
STAGE_LABELS: Mapping[str, str] = {
    "queries": "Queries",
    "discover": "Discover",
    "ingest": "Ingest",
    "mask": "Mask",
    "screen": "Screen",
    "export": "Export",
    "index": "Index",
    "answer": "Brief",
}

# A run whose worker died is resumed this many times before it is failed: often
# enough to ride out a deploy or a node failure, rarely enough that a stage that
# kills its worker every time does not take a worker down forever.
MAX_RECOVERIES = 2

_MAX_ITEM_LENGTH = 300
_EVENT_ID = re.compile(r"[0-9]{1,19}")

AGENT_ROLES = frozenset({"planner", "retriever", "writer", "checks", "critic"})
AGENT_STATUSES = frozenset({"ok", "refused", "invalid", "error"})

RUN_LOST_MESSAGE = (
    "The run stopped unexpectedly and could not be resumed. Finished stages are kept; "
    "start the run again to continue."
)


class RunConflictError(Exception):
    """The run or gate is not in a state that allows this. The message is safe to show."""


def plan_stages(requested: Sequence[str], available: Collection[str]) -> tuple[str, ...]:
    """The stages a run will execute, in pipeline order. None asked for means all of them.

    Refuses an unknown or repeated stage, or one this deployment cannot run, so no run
    starts that is bound to fail.
    """
    requested = requested or STAGES
    unknown = [s for s in requested if s not in STAGES]
    if unknown:
        raise ValueError(f"Run.stages contains unknown stages: {', '.join(sorted(unknown))}")
    if len(set(requested)) != len(requested):
        raise ValueError("Run.stages must not name a stage twice")
    unavailable = [s for s in requested if s not in available]
    if unavailable:
        raise ValueError(
            f"Run.stages contains stages this deployment cannot run: {', '.join(unavailable)}"
        )
    return tuple(s for s in STAGES if s in requested)


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunTrigger(StrEnum):
    CLAIM = "claim"
    PAUSE = "pause"
    RESUME = "resume"
    REQUEUE = "requeue"
    FINISH = "finish"
    FAIL = "fail"
    CANCEL = "cancel"


FINISHED_STATES = frozenset({RunState.DONE, RunState.FAILED, RunState.CANCELLED})
_FINISHING = frozenset({RunTrigger.FINISH, RunTrigger.FAIL, RunTrigger.CANCEL})

_R = RunState
_RT = RunTrigger
_TRANSITIONS: Mapping[tuple[RunState, RunTrigger], RunState] = {
    (_R.QUEUED, _RT.CLAIM): _R.RUNNING,
    (_R.RUNNING, _RT.PAUSE): _R.WAITING,
    (_R.WAITING, _RT.RESUME): _R.QUEUED,
    (_R.RUNNING, _RT.REQUEUE): _R.QUEUED,
    (_R.RUNNING, _RT.FINISH): _R.DONE,
    (_R.RUNNING, _RT.FAIL): _R.FAILED,
    (_R.QUEUED, _RT.CANCEL): _R.CANCELLED,
    (_R.RUNNING, _RT.CANCEL): _R.CANCELLED,
    (_R.WAITING, _RT.CANCEL): _R.CANCELLED,
}


def next_run_state(state: RunState, trigger: RunTrigger) -> RunState:
    """The state `trigger` leads to from `state`, or `InvalidTransitionError`."""
    following = _TRANSITIONS.get((state, trigger))
    if following is None:
        raise InvalidTransitionError("This run is not in a state that allows this.")
    return following


def _require_aware(owner: str, **timestamps: datetime | None) -> None:
    for name, value in timestamps.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Run:
    """One execution of some of a review's stages. Its row is also its job in the queue.

    A stage interrupted by a gate or a crash runs again from its start, so stages must be
    safe to repeat. `stage_started_at` survives that, so `stage.started` is sent once.
    """

    id: UUID
    review_id: UUID
    project_id: UUID
    stages: tuple[str, ...]
    state: RunState
    created_at: datetime
    created_by: UUID
    queued_at: datetime
    stages_finished: int = 0
    stage_started_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    recoveries: int = 0
    # Optimistic locking, as for users and projects.
    version: int = 0

    def __post_init__(self) -> None:
        _require_aware(
            "Run",
            created_at=self.created_at,
            queued_at=self.queued_at,
            stage_started_at=self.stage_started_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            cancel_requested_at=self.cancel_requested_at,
            lease_expires_at=self.lease_expires_at,
        )
        if not self.stages:
            raise ValueError("Run.stages must name at least one stage")
        if plan_stages(self.stages, STAGES) != self.stages:
            raise ValueError("Run.stages must be in pipeline order")
        if not 0 <= self.stages_finished <= len(self.stages):
            raise ValueError("Run.stages_finished must be between 0 and the number of stages")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("Run.lease_owner and lease_expires_at are set together")
        if self.recoveries < 0:
            raise ValueError("Run.recoveries must not be negative")

    @property
    def is_finished(self) -> bool:
        return self.state in FINISHED_STATES

    @property
    def outcome(self) -> RunState | None:
        """`done`, `failed` or `cancelled` once finished; `None` before."""
        return self.state if self.is_finished else None

    @property
    def total(self) -> int:
        return len(self.stages)

    @property
    def current_stage(self) -> str | None:
        """The stage to run next, or `None` once every stage has finished."""
        if self.stages_finished >= self.total:
            return None
        return self.stages[self.stages_finished]

    @property
    def position(self) -> int:
        """The current stage's place in this run, counting from 1."""
        return self.stages_finished + 1

    def seconds(self, now: datetime) -> float:
        """How long the run has been going since a worker first picked it up."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at or now
        return round((end - self.started_at).total_seconds(), 3)

    def has_expired_lease(self, now: datetime) -> bool:
        return (
            self.state is RunState.RUNNING
            and self.lease_expires_at is not None
            and now > self.lease_expires_at
        )

    def _to(self, trigger: RunTrigger) -> RunState:
        return next_run_state(self.state, trigger)

    def claimed(self, worker_id: str, now: datetime, lease: timedelta) -> Run:
        return replace(
            self,
            state=self._to(RunTrigger.CLAIM),
            lease_owner=worker_id,
            lease_expires_at=now + lease,
            started_at=self.started_at or now,
        )

    def stage_started(self, now: datetime) -> Run:
        return replace(self, stage_started_at=now)

    def stage_finished(self) -> Run:
        if self.current_stage is None:
            raise ValueError("Run has no stage left to finish")
        return replace(
            self,
            stages_finished=self.stages_finished + 1,
            stage_started_at=None,
            recoveries=0,
        )

    def paused(self) -> Run:
        """Wait for a person: the run gives its lease, and so its worker, back."""
        return replace(
            self, state=self._to(RunTrigger.PAUSE), lease_owner=None, lease_expires_at=None
        )

    def resumed(self, now: datetime) -> Run:
        return replace(self, state=self._to(RunTrigger.RESUME), queued_at=now)

    def requeued(self, now: datetime) -> Run:
        """Queue again after the worker vanished; counts towards `MAX_RECOVERIES`."""
        return replace(self.released(now), recoveries=self.recoveries + 1)

    def released(self, now: datetime) -> Run:
        """Queue again because the worker is shutting down. Not a crash, so not counted."""
        return replace(
            self,
            state=self._to(RunTrigger.REQUEUE),
            lease_owner=None,
            lease_expires_at=None,
            queued_at=now,
        )

    def finished(self, trigger: RunTrigger, at: datetime) -> Run:
        if trigger not in _FINISHING:
            raise ValueError("Run.finished needs a finishing trigger: finish, fail or cancel")
        return replace(
            self,
            state=self._to(trigger),
            finished_at=at,
            lease_owner=None,
            lease_expires_at=None,
        )

    def with_cancel_requested(self, now: datetime) -> Run:
        """Ask a running run to stop at its next checkpoint. Asking again changes nothing."""
        return replace(self, cancel_requested_at=self.cancel_requested_at or now)


def recovery_for(run: Run) -> RunTrigger:
    """What happens to a run whose lease expired: cancel, fail, or queue it again."""
    if run.cancel_requested_at is not None:
        return RunTrigger.CANCEL
    if run.recoveries >= MAX_RECOVERIES:
        return RunTrigger.FAIL
    return RunTrigger.REQUEUE


def stage_failed_message(stage: str) -> str:
    """What members are told when a stage fails for a reason it did not explain itself."""
    label = STAGE_LABELS.get(stage, "current")
    return (
        f"The {label} stage stopped because of an unexpected error. Finished stages are "
        "kept; try the run again, and tell your administrator if it keeps happening."
    )


class EventName(StrEnum):
    STAGE_STARTED = "stage.started"
    STAGE_PROGRESS = "stage.progress"
    STAGE_FINISHED = "stage.finished"
    AGENT_STEP = "agent.step"
    GATE_REQUIRED = "gate.required"
    GATE_RESOLVED = "gate.resolved"
    JOB_FINISHED = "job.finished"


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One entry in a run's log. `id` counts from 1 within the run, with no gaps."""

    run_id: UUID
    id: int
    name: EventName
    payload_sealed: bytes
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.id < 1:
            raise ValueError("RunEvent.id must be 1 or more")
        if not self.payload_sealed:
            raise ValueError("RunEvent.payload_sealed must not be empty")
        _require_aware("RunEvent", occurred_at=self.occurred_at)


def resume_point(last_event_id: str | None, last_id: int) -> int:
    """The id after which a reconnecting stream resumes; 0 replays from the start.

    An id this run does not have replays everything: the browser may see an event twice,
    but never miss one.
    """
    if last_event_id is None or not _EVENT_ID.fullmatch(last_event_id.strip()):
        return 0
    value = int(last_event_id.strip())
    return value if 1 <= value <= last_id else 0


def stage_started_payload(
    stage: str, position: int, total: int, at: datetime
) -> dict[str, JsonValue]:
    return {"stage": stage, "position": position, "total": total, "at": at.isoformat()}


def stage_progress_payload(
    stage: str,
    done: int,
    total: int,
    item: str | None,
    at: datetime,
    screened: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """One step of a stage. `screened` is the screen stage's row for one paper, if any."""
    if done < 0 or total < 0:
        raise ValueError("stage.progress counts must not be negative")
    label = None if item is None else item[:_MAX_ITEM_LENGTH]
    payload: dict[str, JsonValue] = {
        "stage": stage,
        "done": done,
        "total": total,
        "item": label,
        "at": at.isoformat(),
    }
    if screened is not None:
        payload["screened"] = dict(screened)
    return payload


def stage_finished_payload(
    stage: str, seconds: float, summary: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    return {"stage": stage, "seconds": seconds, "summary": dict(summary)}


def agent_step_payload(step: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """One `AgentStep`: `round`, `role` and `status` are required, the rest is per role."""
    round_number = step.get("round")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 0:
        raise ValueError("agent.step round must be a whole number, 0 for the planner")
    if step.get("role") not in AGENT_ROLES:
        raise ValueError(f"agent.step role must be one of {', '.join(sorted(AGENT_ROLES))}")
    if step.get("status") not in AGENT_STATUSES:
        raise ValueError(
            f"agent.step status must be one of {', '.join(sorted(AGENT_STATUSES))}"
        )
    return dict(step)


def gate_required_payload(
    gate_id: UUID, kind: GateKind, payload: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    return {"gate_id": str(gate_id), "kind": kind.value, "payload": dict(payload)}


def gate_resolved_payload(
    gate_id: UUID, by: UUID, decision: GateDecision
) -> dict[str, JsonValue]:
    return {"gate_id": str(gate_id), "by": str(by), "decision": decision.value}


def job_finished_payload(
    outcome: RunState, seconds: float, error: str | None
) -> dict[str, JsonValue]:
    if outcome not in FINISHED_STATES:
        raise ValueError("job.finished outcome must be done, failed or cancelled")
    return {"outcome": outcome.value, "seconds": seconds, "error": error}


class GateKind(StrEnum):
    VOCABULARY = "vocabulary"
    PRIVACY_REVIEW = "privacy_review"


class GateDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


# Approving masking means reading the documents' raw text, which only owners may;
# confirming a vocabulary is any contributor's.
_GATE_ACTIONS: Mapping[GateKind, Action] = {
    GateKind.VOCABULARY: Action.CONFIRM_GATE,
    GateKind.PRIVACY_REVIEW: Action.MANAGE_DOCUMENTS,
}


def action_to_resolve(kind: GateKind) -> Action:
    return _GATE_ACTIONS[kind]


@dataclass(frozen=True, slots=True)
class Gate:
    """A pause in a run until a person decides. Resolved once; the first decision wins."""

    id: UUID
    run_id: UUID
    project_id: UUID
    stage: str
    kind: GateKind
    payload_sealed: bytes
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: UUID | None = None
    decision: GateDecision | None = None
    revisions_sealed: bytes | None = None

    def __post_init__(self) -> None:
        _require_aware("Gate", created_at=self.created_at, resolved_at=self.resolved_at)
        if self.stage not in STAGES:
            raise ValueError("Gate.stage must be a known stage")
        if not self.payload_sealed:
            raise ValueError("Gate.payload_sealed must not be empty")
        parts = (self.resolved_at, self.resolved_by, self.decision)
        if any(p is None for p in parts) and any(p is not None for p in parts):
            raise ValueError(
                "Gate is resolved exactly when resolved_at, by and decision are set"
            )
        if self.revisions_sealed is not None and self.resolved_at is None:
            raise ValueError("Gate.revisions_sealed belongs to a resolved gate")

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    def resolved(
        self, by: UUID, decision: GateDecision, revisions_sealed: bytes | None, at: datetime
    ) -> Gate:
        return replace(
            self,
            resolved_at=at,
            resolved_by=by,
            decision=decision,
            revisions_sealed=revisions_sealed,
        )


@dataclass(frozen=True, slots=True)
class GateResolution:
    """What a stage learns when it resumes past a gate."""

    decision: GateDecision
    revisions: Mapping[str, JsonValue] | None
    resolved_by: UUID
