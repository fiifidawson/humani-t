"""Needs you and activity: what waits for a person, and what happened in a project.

Both are derived from records that already exist (reviews, plan versions, runs, gates,
briefs, questions, documents, label rules); only a person's dismissals are stored. This
module orders and keys those facts. Gathering them from storage is the application's job.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.domain.runs import GateKind, Run, RunState
from app.domain.timestamps import require_aware

MAX_ITEM_KEY_LENGTH = 120


class NeedsYouKind(StrEnum):
    GATE = "gate"
    DRAFT = "draft"
    FAILED_RUN = "failed_run"
    UNREAD_BRIEF = "unread_brief"
    # A project near its retention date (ADR 0020): about the project, not one review.
    RETENTION = "retention"


# The field that names what each kind of item is about; its key is built from it.
_TARGET_FIELD: Mapping[NeedsYouKind, str] = {
    NeedsYouKind.GATE: "gate_id",
    NeedsYouKind.FAILED_RUN: "run_id",
    NeedsYouKind.DRAFT: "version",
    NeedsYouKind.UNREAD_BRIEF: "brief_id",
    NeedsYouKind.RETENTION: "deletes_at",
}


@dataclass(frozen=True, slots=True)
class NeedsYouItem:
    """One thing in one review waiting for a person, with what its row shows.

    `review_id` is `None` only for a retention notice, which is about the whole project.
    """

    kind: NeedsYouKind
    project_id: UUID
    review_id: UUID | None
    at: datetime
    gate_id: UUID | None = None
    gate_kind: GateKind | None = None
    run_id: UUID | None = None
    run_number: int | None = None
    stopped_at: str | None = None
    # The plan version of a draft, or the brief's version.
    version: int | None = None
    brief_id: UUID | None = None
    deletes_at: datetime | None = None
    days_left: int | None = None

    def __post_init__(self) -> None:
        require_aware("NeedsYouItem", at=self.at, deletes_at=self.deletes_at)
        field = _TARGET_FIELD[self.kind]
        if getattr(self, field) is None:
            raise ValueError(f"NeedsYouItem.{field} is required for a {self.kind.value} item")
        if self.review_id is None and self.kind is not NeedsYouKind.RETENTION:
            raise ValueError(f"NeedsYouItem.review_id is required for a {self.kind.value} item")

    @property
    def key(self) -> str:
        """What a dismissal names. A new run, draft or brief is a new key, so it comes back."""
        if self.kind is NeedsYouKind.DRAFT:
            return f"{self.kind.value}:{self.review_id}:{self.version}"
        if self.kind is NeedsYouKind.RETENTION and self.deletes_at is not None:
            return (
                f"{self.kind.value}:{self.project_id}:{self.deletes_at.astimezone(UTC).date()}"
            )
        return f"{self.kind.value}:{getattr(self, _TARGET_FIELD[self.kind])}"


@dataclass(frozen=True, slots=True)
class Dismissal:
    """A person's "Dismiss" on one Needs you item. Ids only, never project text."""

    user_id: UUID
    item_key: str
    dismissed_at: datetime

    def __post_init__(self) -> None:
        require_aware("Dismissal", dismissed_at=self.dismissed_at)
        if not self.item_key.strip():
            raise ValueError("Dismissal.item_key must not be blank")
        if len(self.item_key) > MAX_ITEM_KEY_LENGTH:
            raise ValueError(f"Dismissal.item_key must be at most {MAX_ITEM_KEY_LENGTH} long")


def still_needed(
    items: Iterable[NeedsYouItem], dismissed: Collection[str]
) -> tuple[NeedsYouItem, ...]:
    """The items the person has not dismissed, newest first."""
    kept = [item for item in items if item.key not in dismissed]
    return tuple(sorted(kept, key=lambda item: item.at, reverse=True))


class ActivityKind(StrEnum):
    """The timeline's "Show:" filters: each action falls under exactly one."""

    REVIEW = "review"
    RUN = "run"
    BRIEF = "brief"
    QUESTION = "question"
    DOCUMENT = "document"


class ActivityAction(StrEnum):
    REVIEW_STARTED = "review_started"
    PLAN_DRAFTED = "plan_drafted"
    PLAN_ACCEPTED = "plan_accepted"
    LABELS_CHANGED = "labels_changed"
    # One line per run, showing its latest state.
    RUN = "run"
    BRIEF_READY = "brief_ready"
    QUESTION_ASKED = "question_asked"
    DOCUMENT_ADDED = "document_added"


_KIND_OF: Mapping[ActivityAction, ActivityKind] = {
    ActivityAction.REVIEW_STARTED: ActivityKind.REVIEW,
    ActivityAction.PLAN_DRAFTED: ActivityKind.REVIEW,
    ActivityAction.PLAN_ACCEPTED: ActivityKind.REVIEW,
    ActivityAction.LABELS_CHANGED: ActivityKind.REVIEW,
    ActivityAction.RUN: ActivityKind.RUN,
    ActivityAction.BRIEF_READY: ActivityKind.BRIEF,
    ActivityAction.QUESTION_ASKED: ActivityKind.QUESTION,
    ActivityAction.DOCUMENT_ADDED: ActivityKind.DOCUMENT,
}


class StageState(StrEnum):
    DONE = "done"
    RUNNING = "running"
    WAITING = "waiting"
    STOPPED = "stopped"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class StageLine:
    """One stage of a run as its expanded timeline line shows it."""

    stage: str
    state: StageState
    seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One L0 line: what happened, when, who did it, and what its one action opens.

    Only the fields its action uses are set. `text` is a review or follow-up question as
    stored (masked when the project masks), or a document's name.
    """

    action: ActivityAction
    at: datetime
    project_id: UUID
    review_id: UUID | None = None
    actor_id: UUID | None = None
    run_id: UUID | None = None
    run_number: int | None = None
    run_state: RunState | None = None
    seconds: float | None = None
    stopped_at: str | None = None
    error: str | None = None
    model_ids: tuple[str, ...] = ()
    stages: tuple[StageLine, ...] = ()
    # The plan version drafted or accepted, the brief's, or the label rules'.
    version: int | None = None
    brief_id: UUID | None = None
    thread_id: UUID | None = None
    document_id: UUID | None = None
    document_number: int | None = None
    text: str | None = None
    papers_moved: int | None = None

    def __post_init__(self) -> None:
        require_aware("ActivityEvent", at=self.at)

    @property
    def kind(self) -> ActivityKind:
        return _KIND_OF[self.action]


@dataclass(frozen=True, slots=True)
class HistoryGroup:
    """A review's events, newest first; `review_id` is `None` for project-wide events."""

    review_id: UUID | None
    events: tuple[ActivityEvent, ...]

    @property
    def latest_at(self) -> datetime:
        return self.events[0].at


def newest_first(events: Iterable[ActivityEvent]) -> tuple[ActivityEvent, ...]:
    return tuple(sorted(events, key=lambda event: event.at, reverse=True))


def group_by_review(events: Iterable[ActivityEvent]) -> tuple[HistoryGroup, ...]:
    """Group events by review, newest first inside each; groups by their latest event."""
    grouped: dict[UUID | None, list[ActivityEvent]] = {}
    for event in events:
        grouped.setdefault(event.review_id, []).append(event)
    groups = [HistoryGroup(review, newest_first(listed)) for review, listed in grouped.items()]
    return tuple(sorted(groups, key=lambda group: group.latest_at, reverse=True))


def run_numbers(runs: Sequence[Run]) -> dict[UUID, int]:
    """Each run's number within its review, counting from 1 in the order they started."""
    ordered = sorted(runs, key=lambda run: (run.created_at, run.id))
    return {run.id: number for number, run in enumerate(ordered, start=1)}


def stopped_at(run: Run) -> str | None:
    """The stage a failed or cancelled run did not finish; `None` for any other run."""
    if run.state in (RunState.FAILED, RunState.CANCELLED):
        return run.current_stage
    return None


_CURRENT_STATE: Mapping[RunState, StageState] = {
    RunState.RUNNING: StageState.RUNNING,
    RunState.WAITING: StageState.WAITING,
    RunState.FAILED: StageState.STOPPED,
    RunState.CANCELLED: StageState.STOPPED,
}


def stage_lines(run: Run, finished: Mapping[str, float]) -> tuple[StageLine, ...]:
    """The run's stages: done with their seconds, the current one's state, the rest not run.

    `finished` holds the seconds each finished stage reported in its `stage.finished`.
    """
    lines: list[StageLine] = []
    for position, stage in enumerate(run.stages):
        if position < run.stages_finished:
            lines.append(StageLine(stage, StageState.DONE, finished.get(stage)))
        elif position == run.stages_finished and run.state in _CURRENT_STATE:
            lines.append(StageLine(stage, _CURRENT_STATE[run.state]))
        else:
            lines.append(StageLine(stage, StageState.NOT_RUN))
    return tuple(lines)
