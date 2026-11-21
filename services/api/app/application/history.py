"""Needs you, Recent activity and a project's timeline, derived from records already kept.

Nothing here keeps its own event log: each call reads reviews, plan versions, runs and their
logs, gates, briefs, questions, documents and label rules in one short transaction, and the
only thing ever stored is a person's dismissal. Only projects the caller is a member of are
read at all, so nothing about any other project can leak into a line, a count or an order.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.accounts import Current
from app.application.documents import DocumentSeal
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.projects import name_context
from app.application.questions import asked_text
from app.application.retention import deletion_date
from app.application.reviews import question_context
from app.application.run_log import RunLog
from app.domain.accounts import NotFoundError
from app.domain.history import (
    ActivityAction,
    ActivityEvent,
    Dismissal,
    NeedsYouItem,
    NeedsYouKind,
    group_by_review,
    run_numbers,
    stage_lines,
    still_needed,
    stopped_at,
)
from app.domain.manifest import StageSummary, assemble_manifest
from app.domain.projects import Action, Project, Role, can
from app.domain.retention import WARNING_DAYS, RetentionPolicy, days_left, is_near
from app.domain.reviews import Review, ReviewStatus
from app.domain.runs import EventName, Gate, GateKind, Run, RunState, action_to_resolve
from app.storage.base import StorageProvider, UnitOfWork

RECENT_ACTIVITY_LIMIT = 40

_NO_PROJECT = "No such project."
_NO_ITEM = "Nothing waiting for you has this key."
_UNKNOWN_AUTHOR = "Unknown account"
_EVENTS_BATCH = 10_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """A project as a line names it. Only ever built for a project the caller belongs to."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class NeedsYouLine:
    item: NeedsYouItem
    project: ProjectRef


@dataclass(frozen=True, slots=True)
class ActivityLine:
    event: ActivityEvent
    project: ProjectRef
    actor_name: str | None


@dataclass(frozen=True, slots=True)
class ReviewRef:
    id: UUID
    question: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class TimelineGroup:
    """A review's lines, newest first; `review` is `None` for project-wide lines."""

    review: ReviewRef | None
    lines: tuple[ActivityLine, ...]


class HistoryService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        retention: RetentionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._documents = DocumentSeal(encryption)
        self._log = RunLog(encryption)
        self._retention = retention or RetentionPolicy()
        self._clock = clock

    async def needs_you(self, current: Current) -> tuple[NeedsYouLine, ...]:
        """What waits for the caller across their live, unarchived projects, newest first.

        A retention notice is the exception: an archived project is still deleted on its date.
        """
        async with self._storage.unit_of_work() as uow:
            lines = await self._needs_you(uow, current.user.id)
            dismissed = {
                d.item_key for d in await uow.dismissals.list_for_user(current.user.id)
            }
        kept = still_needed([line.item for line in lines], dismissed)
        by_key = {line.item.key: line for line in lines}
        return tuple(by_key[item.key] for item in kept)

    async def dismiss(self, current: Current, key: str) -> None:
        """Hide one item for the caller. `NotFoundError` unless it is waiting for them now."""
        async with self._storage.unit_of_work() as uow:
            lines = await self._needs_you(uow, current.user.id)
            if key not in {line.item.key for line in lines}:
                raise NotFoundError(_NO_ITEM)
            # Ids only, and per person: it says nothing another member could not see.
            if await uow.dismissals.add(Dismissal(current.user.id, key, self._clock())):
                await uow.commit()

    async def recent_activity(
        self, current: Current, limit: int = RECENT_ACTIVITY_LIMIT
    ) -> tuple[ActivityLine, ...]:
        """The newest lines across the caller's live, unarchived projects."""
        lines: list[ActivityLine] = []
        async with self._storage.unit_of_work() as uow:
            names = _ActorNames(uow)
            for project, _ in await _projects_of(uow, current.user.id):
                ref = await self._ref(uow, project)
                events = await self._events(uow, project.id, is_detailed=False)
                lines += [ActivityLine(e, ref, await names.of(e.actor_id)) for e in events]
        lines.sort(key=lambda line: line.event.at, reverse=True)
        return tuple(lines[:limit])

    async def timeline(self, current: Current, project_id: UUID) -> tuple[TimelineGroup, ...]:
        """Every line of the project, grouped by review; runs carry their stages."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            project = await uow.projects.get(project_id)
            if project is None:
                raise NotFoundError(_NO_PROJECT)
            ref = await self._ref(uow, project)
            events = await self._events(uow, project_id, is_detailed=True)
            names = _ActorNames(uow)
            reviews = {
                e.review_id: ReviewRef(e.review_id, e.text or "", e.at)
                for e in events
                if e.action is ActivityAction.REVIEW_STARTED and e.review_id is not None
            }
            return tuple(
                [
                    TimelineGroup(
                        None if group.review_id is None else reviews.get(group.review_id),
                        tuple(
                            [
                                ActivityLine(e, ref, await names.of(e.actor_id))
                                for e in group.events
                            ]
                        ),
                    )
                    for group in group_by_review(events)
                ]
            )

    async def _needs_you(self, uow: UnitOfWork, user_id: UUID) -> list[NeedsYouLine]:
        read = {r.brief_id for r in await uow.marks.list_brief_reads(user_id)}
        lines: list[NeedsYouLine] = []
        for project, role in await _projects_of(uow, user_id, include_archived=True):
            items = (
                []
                if project.is_archived
                else [
                    item
                    for review in await uow.reviews.list_for_project(project.id)
                    for item in await _waiting_in(uow, review, role, read)
                ]
            )
            notice = await self._retention_notice(uow, project, role)
            if notice is not None:
                items.append(notice)
            if items:
                ref = await self._ref(uow, project)
                lines += [NeedsYouLine(item, ref) for item in items]
        return lines

    async def _retention_notice(
        self, uow: UnitOfWork, project: Project, role: Role
    ) -> NeedsYouItem | None:
        """The "Deleted in N days unless kept" notice, for owners who can keep the project."""
        if not can(role, Action.UPDATE_PROJECT):
            return None
        deletes_at = await deletion_date(uow, project, self._retention)
        now = self._clock()
        if deletes_at is None or not is_near(deletes_at, now):
            return None
        return NeedsYouItem(
            kind=NeedsYouKind.RETENTION,
            project_id=project.id,
            review_id=None,
            at=deletes_at - timedelta(days=WARNING_DAYS),
            deletes_at=deletes_at,
            days_left=days_left(deletes_at, now),
        )

    async def _events(
        self, uow: UnitOfWork, project_id: UUID, *, is_detailed: bool
    ) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for review in await uow.reviews.list_for_project(project_id):
            events += await self._review_events(uow, review, is_detailed=is_detailed)
        for document in await uow.documents.list_for_project(project_id):
            events.append(
                ActivityEvent(
                    action=ActivityAction.DOCUMENT_ADDED,
                    at=document.uploaded_at,
                    project_id=project_id,
                    actor_id=document.uploaded_by,
                    document_id=document.id,
                    document_number=document.number,
                    text=await self._documents.name(uow, document),
                )
            )
        return events

    async def _review_events(
        self, uow: UnitOfWork, review: Review, *, is_detailed: bool
    ) -> list[ActivityEvent]:
        question = await self._encryption.decrypt(
            uow, question_context(review.project_id, review.id), review.question_sealed
        )
        events = [
            ActivityEvent(
                action=ActivityAction.REVIEW_STARTED,
                at=review.created_at,
                project_id=review.project_id,
                review_id=review.id,
                actor_id=review.created_by,
                text=question.decode(),
            )
        ]
        events += await _plan_events(uow, review)
        runs = await uow.runs.list_for_review(review.id)
        numbers = run_numbers(runs)
        for run in runs:
            events.append(await self._run_event(uow, run, numbers[run.id], is_detailed))
            brief = await uow.briefs.for_run(run.id)
            if brief is not None:
                events.append(
                    ActivityEvent(
                        action=ActivityAction.BRIEF_READY,
                        at=brief.created_at,
                        project_id=review.project_id,
                        review_id=review.id,
                        run_id=run.id,
                        run_number=numbers[run.id],
                        brief_id=brief.id,
                        version=brief.version,
                        model_ids=(brief.model_id,),
                    )
                )
        for thread in await uow.questions.list_threads(review.id):
            for record in await uow.questions.list_for_thread(thread.id):
                events.append(
                    ActivityEvent(
                        action=ActivityAction.QUESTION_ASKED,
                        at=record.asked_at,
                        project_id=review.project_id,
                        review_id=review.id,
                        actor_id=record.asked_by,
                        thread_id=thread.id,
                        model_ids=() if record.model_id is None else (record.model_id,),
                        text=await asked_text(self._encryption, uow, record),
                    )
                )
        for rules in await uow.label_rules.list_for_review(review.id):
            events.append(
                ActivityEvent(
                    action=ActivityAction.LABELS_CHANGED,
                    at=rules.created_at,
                    project_id=review.project_id,
                    review_id=review.id,
                    actor_id=rules.created_by,
                    version=rules.version,
                    papers_moved=rules.papers_moved,
                )
            )
        return events

    async def _run_event(
        self, uow: UnitOfWork, run: Run, number: int, is_detailed: bool
    ) -> ActivityEvent:
        """One line for the run; with `is_detailed`, its stages, models and error from its log.

        Home leaves the log unread: it shows only the run's state.
        """
        finished: list[StageSummary] = []
        error: str | None = None
        if is_detailed:
            for event in await self._log.read(uow, run, 0, _EVENTS_BATCH):
                data = event.data
                if event.name == EventName.STAGE_FINISHED.value:
                    stage, seconds, summary = (
                        data.get("stage"),
                        data.get("seconds"),
                        data.get("summary"),
                    )
                    if (
                        isinstance(stage, str)
                        and isinstance(seconds, int | float)
                        and isinstance(summary, dict)
                    ):
                        finished.append(StageSummary(stage, float(seconds), summary))
                elif event.name == EventName.JOB_FINISHED.value:
                    reported = data.get("error")
                    error = reported if isinstance(reported, str) else None
        return ActivityEvent(
            action=ActivityAction.RUN,
            at=run.finished_at or run.started_at or run.created_at,
            project_id=run.project_id,
            review_id=run.review_id,
            actor_id=run.created_by,
            run_id=run.id,
            run_number=number,
            run_state=run.state,
            seconds=run.seconds(self._clock()) if run.started_at else None,
            stopped_at=stopped_at(run),
            error=error,
            model_ids=assemble_manifest(run, finished).model_ids,
            stages=stage_lines(run, {s.stage: s.seconds for s in finished})
            if is_detailed
            else (),
        )

    async def _ref(self, uow: UnitOfWork, project: Project) -> ProjectRef:
        name = await self._encryption.decrypt(
            uow, name_context(project.id), project.name_sealed
        )
        return ProjectRef(project.id, name.decode())


async def _projects_of(
    uow: UnitOfWork, user_id: UUID, *, include_archived: bool = False
) -> list[tuple[Project, Role]]:
    """The caller's live projects with their role in each; archived ones only if asked."""
    found: list[tuple[Project, Role]] = []
    for project in await uow.projects.list_for_member(user_id):
        membership = await uow.memberships.get(project.id, user_id)
        if membership is not None and (include_archived or not project.is_archived):
            found.append((project, membership.role))
    return found


async def _waiting_in(
    uow: UnitOfWork, review: Review, role: Role, read: set[UUID]
) -> list[NeedsYouItem]:
    """What in one review waits for a member with `role`: only what that role can act on."""
    items: list[NeedsYouItem] = []
    runs = await uow.runs.list_for_review(review.id)
    numbers = run_numbers(runs)
    latest = runs[0] if runs else None
    if latest is not None and latest.state is RunState.WAITING:
        gate = await _open_gate(uow, latest)
        if gate is not None and can(role, action_to_resolve(gate.kind)):
            items.append(
                NeedsYouItem(
                    kind=NeedsYouKind.GATE,
                    project_id=review.project_id,
                    review_id=review.id,
                    at=gate.created_at,
                    gate_id=gate.id,
                    gate_kind=gate.kind,
                    run_id=latest.id,
                    run_number=numbers[latest.id],
                )
            )
    # A failure stops waiting once a newer run starts: only the latest run is looked at.
    if latest is not None and latest.state is RunState.FAILED and can(role, Action.MANAGE_RUNS):
        items.append(
            NeedsYouItem(
                kind=NeedsYouKind.FAILED_RUN,
                project_id=review.project_id,
                review_id=review.id,
                at=latest.finished_at or latest.created_at,
                run_id=latest.id,
                run_number=numbers[latest.id],
                stopped_at=stopped_at(latest),
            )
        )
    if review.status is ReviewStatus.DRAFTING and can(role, Action.EDIT_REVIEW):
        draft = await uow.plans.latest_version(review.id)
        if draft is not None and not draft.is_accepted:
            items.append(
                NeedsYouItem(
                    kind=NeedsYouKind.DRAFT,
                    project_id=review.project_id,
                    review_id=review.id,
                    at=draft.created_at,
                    version=draft.version,
                )
            )
    brief = await uow.briefs.latest_for_review(review.id)
    if brief is not None and brief.id not in read:
        items.append(
            NeedsYouItem(
                kind=NeedsYouKind.UNREAD_BRIEF,
                project_id=review.project_id,
                review_id=review.id,
                at=brief.created_at,
                version=brief.version,
                brief_id=brief.id,
                run_id=brief.run_id,
            )
        )
    return items


async def _open_gate(uow: UnitOfWork, run: Run) -> Gate | None:
    stage = run.current_stage
    if stage is None:
        return None
    for kind in GateKind:
        gate = await uow.gates.find(run.id, stage, kind)
        if gate is not None and not gate.is_resolved:
            return gate
    return None


async def _plan_events(uow: UnitOfWork, review: Review) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []
    for version in await uow.plans.list_versions(review.id):
        events.append(
            ActivityEvent(
                action=ActivityAction.PLAN_DRAFTED,
                at=version.created_at,
                project_id=review.project_id,
                review_id=review.id,
                actor_id=version.author.user_id,
                version=version.version,
            )
        )
        if version.accepted_at is not None:
            events.append(
                ActivityEvent(
                    action=ActivityAction.PLAN_ACCEPTED,
                    at=version.accepted_at,
                    project_id=review.project_id,
                    review_id=review.id,
                    actor_id=version.accepted_by,
                    version=version.version,
                )
            )
    return events


class _ActorNames:
    """Display names by user id, each looked up once per call."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._names: dict[UUID, str] = {}

    async def of(self, user_id: UUID | None) -> str | None:
        if user_id is None:
            return None
        if user_id not in self._names:
            user = await self._uow.users.get(user_id)
            self._names[user_id] = _UNKNOWN_AUTHOR if user is None else user.display_name
        return self._names[user_id]
