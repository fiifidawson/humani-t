"""A run's event log, and the one way a run finishes, shared by the API and the worker.

Every event is stored, sealed and bound to its run and id, before anyone is told; a stream
only reads this log. Events commit in the caller's unit of work, so there is never a
`job.finished` without the run having finished, nor the reverse.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.encryption import ScopeEncryption
from app.domain.encryption import EncryptionContext, KeyDestroyedError
from app.domain.reviews import ReviewTrigger
from app.domain.runs import (
    EventName,
    JsonValue,
    Run,
    RunEvent,
    RunTrigger,
    job_finished_payload,
)
from app.storage.base import UnitOfWork

QUEUE_CHANNEL = "runs:queued"

_EVENT_PURPOSE = "run_events.payload"

_REVIEW_TRIGGER = {
    RunTrigger.FINISH: ReviewTrigger.FINISH,
    RunTrigger.FAIL: ReviewTrigger.FAIL,
    RunTrigger.CANCEL: ReviewTrigger.CANCEL,
    RunTrigger.PAUSE: ReviewTrigger.PAUSE,
    RunTrigger.RESUME: ReviewTrigger.RESUME,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_channel(run_id: UUID) -> str:
    """The signal channel streams of one run wait on."""
    return f"run:{run_id}"


def to_json(value: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode()


def from_json(data: bytes) -> dict[str, JsonValue]:
    loaded: dict[str, JsonValue] = json.loads(data)
    return loaded


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One decrypted event, as a stream sends it."""

    id: int
    name: str
    data: Mapping[str, JsonValue]


class RunLog:
    def __init__(
        self, encryption: ScopeEncryption, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._encryption = encryption
        self._clock = clock

    async def append(
        self,
        uow: UnitOfWork,
        run: Run,
        name: EventName,
        payload: Mapping[str, JsonValue],
        *,
        lease_owner: str | None = None,
    ) -> RunEvent | None:
        """Store the run's next event. `None` if the run may not take one (see `next_id`).

        Raises `KeyDestroyedError` once the project has been deleted.
        """
        event_id = await uow.run_events.next_id(run.id, lease_owner=lease_owner)
        if event_id is None:
            return None
        return await self._add(uow, run, event_id, name, payload)

    async def read(
        self, uow: UnitOfWork, run: Run, after: int, limit: int
    ) -> list[StreamEvent]:
        """Decrypt events after `after`. Raises `KeyDestroyedError` once the project is gone."""
        events = await uow.run_events.list_after(run.id, after, limit)
        return [
            StreamEvent(
                event.id,
                event.name.value,
                from_json(
                    await self._encryption.decrypt(
                        uow,
                        _event_context(run.project_id, run.id, event.id),
                        event.payload_sealed,
                    )
                ),
            )
            for event in events
        ]

    async def finish(
        self,
        uow: UnitOfWork,
        run: Run,
        trigger: RunTrigger,
        error: str | None,
        *,
        lease_owner: str | None = None,
    ) -> Run | None:
        """End `run` — read under its lock — with exactly one `job.finished`, its last event.

        `None` if it had already finished or `lease_owner` no longer holds it. A run of a
        deleted project ends with no event, since its log can never be read again.
        """
        finished = run.finished(trigger, self._clock())
        event_id = await uow.run_events.next_id(run.id, lease_owner=lease_owner)
        if event_id is None:
            return None
        payload = job_finished_payload(finished.state, finished.seconds(self._clock()), error)
        # Ending the run must not depend on the key: a run that could not end
        # once its project was shredded would hold the queue's recovery forever.
        with contextlib.suppress(KeyDestroyedError):
            await self._add(uow, run, event_id, EventName.JOB_FINISHED, payload)
        saved = await uow.runs.save(finished)
        await self.move_review(uow, run.review_id, trigger)
        return saved

    async def _add(
        self,
        uow: UnitOfWork,
        run: Run,
        event_id: int,
        name: EventName,
        payload: Mapping[str, JsonValue],
    ) -> RunEvent:
        sealed = await self._encryption.encrypt(
            uow, _event_context(run.project_id, run.id, event_id), to_json(payload)
        )
        event = RunEvent(run.id, event_id, name, sealed, self._clock())
        await uow.run_events.add(event)
        return event

    @staticmethod
    async def move_review(uow: UnitOfWork, review_id: UUID, trigger: RunTrigger) -> None:
        """Keep the review's status in step with its run."""
        review = await uow.reviews.get(review_id)
        if review is None:
            raise ValueError(f"review {review_id} does not exist")
        await uow.reviews.save(review.transitioned(_REVIEW_TRIGGER[trigger]))


def _event_context(project_id: UUID, run_id: UUID, event_id: int) -> EncryptionContext:
    return EncryptionContext(project_id, _EVENT_PURPOSE, f"{run_id}:{event_id}")


def gate_payload_context(project_id: UUID, gate_id: UUID) -> EncryptionContext:
    """How a gate's payload is bound: table "gates", column "payload", the gate."""
    return EncryptionContext(project_id, "gates.payload", str(gate_id))


def gate_revisions_context(project_id: UUID, gate_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, "gates.revisions", str(gate_id))
