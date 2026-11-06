"""Runs from the API's side: start one, cancel one, decide a gate, follow the stream.

The worker that executes runs is in `run_worker.py`; both obey the same rules
and write through the same log (`run_log.py`). Each use case here is one short
transaction that checks membership first, then signals after it commits.

A run, gate or event route does not name its project. Each use case resolves
the project from the run or gate it names, so a non-member gets the same 404
for an id that exists as for one that does not.

The stream (`open_stream`) never holds a database connection while it waits:
it reads the log in a short transaction, sends what it found, then waits for a
signal or its poll interval with no transaction open (architecture §5.7).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.run_log import (
    QUEUE_CHANNEL,
    RunLog,
    StreamEvent,
    gate_revisions_context,
    run_channel,
    to_json,
)
from app.domain.accounts import NotFoundError
from app.domain.encryption import KeyDestroyedError
from app.domain.projects import Action, can
from app.domain.reviews import Review, ReviewStatus, ReviewTrigger
from app.domain.runs import (
    EventName,
    Gate,
    GateDecision,
    JsonValue,
    Run,
    RunConflictError,
    RunState,
    RunTrigger,
    action_to_resolve,
    gate_resolved_payload,
    plan_stages,
    resume_point,
)
from app.domain.sessions import SessionPolicy
from app.signals.base import Signals
from app.storage.base import StorageProvider, UnitOfWork

__all__ = ["KeepAlive", "RunService", "StreamEvent", "StreamItem"]

# Architecture §4.4: a comment every 15 seconds keeps proxies from closing a
# quiet connection, and the session is re-checked at least once a minute.
KEEP_ALIVE_INTERVAL = timedelta(seconds=15)
SESSION_RECHECK_INTERVAL = timedelta(minutes=1)
# How long a stream waits for a signal before it reads the log anyway. Signals
# make it prompt; this bounds the delay when one is lost.
_POLL_INTERVAL = timedelta(seconds=2)
_READ_BATCH = 500

_RUN_NOT_FOUND = "No such run."
_GATE_NOT_FOUND = "No such gate."
_REVIEW_NOT_FOUND = "No such review."


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class KeepAlive:
    """Nothing happened; say so, so the connection is not closed as idle."""


type StreamItem = StreamEvent | KeepAlive


class RunService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        signals: Signals,
        available_stages: Collection[str],
        session_policy: SessionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
        poll_interval: timedelta = _POLL_INTERVAL,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._signals = signals
        self._available = frozenset(available_stages)
        self._session_policy = session_policy or SessionPolicy()
        self._clock = clock
        self._poll_interval = poll_interval
        self._log = RunLog(encryption, clock)

    async def authorize_review(self, current: Current, review_id: UUID, action: Action) -> None:
        """Check access without acting, so the HTTP layer refuses before reading a body."""
        async with self._storage.unit_of_work() as uow:
            await self._review(uow, current, review_id, action)

    async def authorize_run(self, current: Current, run_id: UUID, action: Action) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._run(uow, current, run_id, action)

    async def authorize_gate(self, current: Current, gate_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._gate(uow, current, gate_id)

    async def start(
        self,
        current: Current,
        review_id: UUID,
        stages: Sequence[str],
        request_id: str | None = None,
    ) -> Run:
        """Queue a run of `stages` and return at once; a worker picks it up.

        Raises `ValueError` for an unusable stage list, after the membership check.
        """
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.MANAGE_RUNS)
            plan = plan_stages(stages, self._available)
            if review.status in (ReviewStatus.RUNNING, ReviewStatus.WAITING):
                raise RunConflictError(
                    "A run is already going for this review. Wait for it, or cancel it."
                )
            await uow.reviews.save(review.transitioned(ReviewTrigger.START_RUN))
            run = Run(
                id=uuid.uuid4(),
                review_id=review.id,
                project_id=review.project_id,
                stages=plan,
                state=RunState.QUEUED,
                created_at=now,
                created_by=current.user.id,
                queued_at=now,
            )
            await uow.runs.add(run)
            await self._record(uow, current, request_id, "run.started", "run", run.id, run)
            await uow.commit()
        await self._signals.publish(QUEUE_CHANNEL)
        return run

    async def list_for_review(self, current: Current, review_id: UUID) -> list[Run]:
        """The review's runs, newest first."""
        async with self._storage.unit_of_work() as uow:
            await self._review(uow, current, review_id, Action.READ_PROJECT)
            return await uow.runs.list_for_review(review_id)

    async def cancel(
        self, current: Current, run_id: UUID, request_id: str | None = None
    ) -> Run:
        """Stop a run. Finished stages are kept.

        A queued or waiting run ends at once. A running one is asked to stop and
        ends at its stage's next checkpoint; the stream reports when.
        """
        async with self._storage.unit_of_work() as uow:
            run = await self._run(uow, current, run_id, Action.MANAGE_RUNS, lock=True)
            if run.is_finished:
                raise RunConflictError("This run has already finished.")
            if run.state is RunState.RUNNING:
                changed = await uow.runs.save(run.with_cancel_requested(self._clock()))
            else:
                finished = await self._log.finish(uow, run, RunTrigger.CANCEL, None)
                if finished is None:
                    raise RunConflictError("This run has already finished.")
                changed = finished
            await self._record(uow, current, request_id, "run.cancelled", "run", run.id, run)
            await uow.commit()
        await self._signals.publish(run_channel(run_id))
        return changed

    async def resolve_gate(
        self,
        current: Current,
        gate_id: UUID,
        decision: GateDecision,
        revisions: Mapping[str, JsonValue] | None,
        request_id: str | None = None,
    ) -> Gate:
        """Decide a gate and queue the rest of the run. The first decision wins; then 409."""
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            gate = await self._gate(uow, current, gate_id)
            run = await uow.runs.lock(gate.run_id)
            if gate.is_resolved:
                raise RunConflictError("Someone already decided this. Showing their decision.")
            if run is None or run.state is not RunState.WAITING:
                raise RunConflictError("This run is no longer waiting for a decision.")
            sealed = None
            if revisions is not None:
                sealed = await self._encryption.encrypt(
                    uow,
                    gate_revisions_context(gate.project_id, gate.id),
                    to_json(revisions),
                )
            resolved = gate.resolved(current.user.id, decision, sealed, now)
            if not await uow.gates.resolve(resolved):
                raise RunConflictError("Someone already decided this. Showing their decision.")
            await self._log.append(
                uow,
                run,
                EventName.GATE_RESOLVED,
                gate_resolved_payload(gate.id, current.user.id, decision),
            )
            await uow.runs.save(run.resumed(now))
            await self._log.move_review(uow, run.review_id, RunTrigger.RESUME)
            await self._record(uow, current, request_id, "gate.resolved", "gate", gate.id, run)
            await uow.commit()
        await self._signals.publish(run_channel(run.id))
        await self._signals.publish(QUEUE_CHANNEL)
        return resolved

    async def open_stream(
        self, current: Current, run_id: UUID, last_event_id: str | None
    ) -> AsyncGenerator[StreamItem]:
        """Check access now, then return the run's events from after `last_event_id`.

        The stream replays what is stored, then follows the run live, with a
        keep-alive while it is quiet. It ends after `job.finished`, or when the
        caller's session or membership ends.
        """
        async with self._storage.unit_of_work() as uow:
            run = await self._run(uow, current, run_id, Action.READ_PROJECT)
            after = resume_point(last_event_id, await uow.run_events.last_id(run_id))
        return self._follow(current, run, after)

    async def _follow(
        self, current: Current, run: Run, after: int
    ) -> AsyncGenerator[StreamItem]:
        last_sent = last_checked = self._clock()
        while True:
            try:
                async with self._storage.unit_of_work() as uow:
                    events = await self._log.read(uow, run, after, _READ_BATCH)
                    latest = await uow.runs.get(run.id)
            except KeyDestroyedError:
                return  # the project was deleted while someone watched
            for event in events:
                yield event
                after, last_sent = event.id, self._clock()
                if event.name == EventName.JOB_FINISHED:
                    return
            if len(events) == _READ_BATCH:
                continue
            if latest is None or latest.is_finished:
                return
            now = self._clock()
            if now - last_checked >= SESSION_RECHECK_INTERVAL:
                if not await self._still_allowed(current, run.project_id):
                    return
                last_checked = now
            if now - last_sent >= KEEP_ALIVE_INTERVAL:
                yield KeepAlive()
                last_sent = now
            await self._signals.wait(run_channel(run.id), self._poll_interval.total_seconds())

    async def _still_allowed(self, current: Current, project_id: UUID) -> bool:
        """Whether the session is still live and its user still a member. Extends nothing.

        Reading the stream is not activity: a tab left open must still reach
        the idle timeout, so this check never touches the session.
        """
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            session = await uow.sessions.get(current.session.id)
            user = await uow.users.get(current.user.id)
            project = await uow.projects.get(project_id)
            membership = await uow.memberships.get(project_id, current.user.id)
        return (
            session is not None
            and session.is_active(now, self._session_policy)
            and user is not None
            and not user.is_disabled
            and project is not None
            and not project.is_deleted
            and membership is not None
            and can(membership.role, Action.READ_PROJECT)
        )

    async def _review(
        self, uow: UnitOfWork, current: Current, review_id: UUID, action: Action
    ) -> Review:
        review = await uow.reviews.get(review_id)
        if review is None:
            raise NotFoundError(_REVIEW_NOT_FOUND)
        await require_member(
            uow, current.user.id, review.project_id, action, not_found=_REVIEW_NOT_FOUND
        )
        return review

    async def _run(
        self,
        uow: UnitOfWork,
        current: Current,
        run_id: UUID,
        action: Action,
        *,
        lock: bool = False,
    ) -> Run:
        run = await (uow.runs.lock if lock else uow.runs.get)(run_id)
        if run is None:
            raise NotFoundError(_RUN_NOT_FOUND)
        await require_member(
            uow, current.user.id, run.project_id, action, not_found=_RUN_NOT_FOUND
        )
        return run

    async def _gate(self, uow: UnitOfWork, current: Current, gate_id: UUID) -> Gate:
        gate = await uow.gates.get(gate_id)
        if gate is None:
            raise NotFoundError(_GATE_NOT_FOUND)
        await require_member(
            uow,
            current.user.id,
            gate.project_id,
            action_to_resolve(gate.kind),
            not_found=_GATE_NOT_FOUND,
        )
        return gate

    async def _record(
        self,
        uow: UnitOfWork,
        current: Current,
        request_id: str | None,
        action: str,
        target_type: str,
        target_id: UUID,
        run: Run,
    ) -> None:
        await self._audit.record(
            uow,
            current.actor(request_id),
            action,
            target_type,
            str(target_id),
            scope_id=run.project_id,
        )
