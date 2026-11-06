"""The worker: claims queued runs and executes their stages, one run at a time.

A run is claimed under a lease (architecture §5.7: no lock is held while work
happens). The worker renews the lease while a stage runs; if the worker dies,
the lease expires and any worker resumes the run, or fails it after
`MAX_RECOVERIES`, so no run is ever stuck as "running".

Every change follows one shape: a short transaction that locks the run, checks
the lease is still this worker's, appends the event and saves the run; then a
signal, after the commit. A worker that lost its lease finds every write
refused and stops without a sound, so two workers never drive one run.

A stage runs with no transaction open. It reaches the run only through its
`StageContext`, each call of which is one of those short transactions.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from app.application.encryption import ScopeEncryption
from app.application.run_log import (
    QUEUE_CHANNEL,
    RunLog,
    from_json,
    gate_payload_context,
    gate_revisions_context,
    run_channel,
    to_json,
)
from app.domain.encryption import KeyDestroyedError
from app.domain.errors import StorageUnavailableError
from app.domain.runs import (
    RUN_LOST_MESSAGE,
    EventName,
    Gate,
    GateKind,
    GateResolution,
    JsonValue,
    Run,
    RunState,
    RunTrigger,
    agent_step_payload,
    gate_required_payload,
    recovery_for,
    stage_failed_message,
    stage_finished_payload,
    stage_progress_payload,
    stage_started_payload,
)
from app.runs.base import Stage, StageFailedError
from app.signals.base import Signals
from app.storage.base import StorageProvider, UnitOfWork

_RECOVERY_BATCH = 10

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _nothing() -> None:
    return None


class _Interrupted(BaseException):
    """Ends one execution of a stage.

    A `BaseException`, like `asyncio.CancelledError`, so that a stage's own
    `except Exception` cannot swallow it by accident.
    """


class _Paused(_Interrupted):
    """The run is waiting at a gate; the worker is free."""


class _Cancelled(_Interrupted):
    """Someone cancelled the run; it ends at this checkpoint."""


class _LeaseLost(_Interrupted):
    """Another worker holds the run now, or it ended; this one must stop writing."""


@dataclass(frozen=True, slots=True)
class _Writer:
    """What every write needs: storage, the log, signals and whose lease to check."""

    storage: StorageProvider
    encryption: ScopeEncryption
    log: RunLog
    signals: Signals
    clock: Callable[[], datetime]
    worker_id: str

    async def append(
        self, uow: UnitOfWork, run: Run, name: EventName, payload: Mapping[str, JsonValue]
    ) -> bool:
        """Append under this worker's lease; False if the lease is gone."""
        event = await self.log.append(uow, run, name, payload, lease_owner=self.worker_id)
        return event is not None


class RunWorker:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        signals: Signals,
        stages: Mapping[str, Stage],
        worker_id: str,
        clock: Callable[[], datetime] = _utc_now,
        lease: timedelta = timedelta(seconds=60),
        poll_interval: timedelta = timedelta(seconds=5),
    ) -> None:
        self._writer = _Writer(
            storage, encryption, RunLog(encryption, clock), signals, clock, worker_id
        )
        self._stages = stages
        self._lease = lease
        self._poll_interval = poll_interval
        self._on_alive: Callable[[], None] = _nothing

    @property
    def worker_id(self) -> str:
        return self._writer.worker_id

    async def serve(
        self, stop: asyncio.Event, *, on_alive: Callable[[], None] = _nothing
    ) -> None:
        """Take runs until `stop` is set. A run in progress is handed back, not abandoned.

        `on_alive` is called at least once per poll interval while the worker is
        healthy: on every turn of the loop, and on every lease renewal while a
        stage runs. The process turns it into a heartbeat file for Compose.
        """
        self._on_alive = on_alive
        while not stop.is_set():
            self._on_alive()
            work = asyncio.create_task(self.run_once())
            stopping = asyncio.create_task(stop.wait())
            await asyncio.wait({work, stopping}, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            if not work.done():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await work
                return
            if not self._worked(work):
                await self._idle(stop)

    async def run_once(self) -> bool:
        """Recover expired leases, then execute one queued run as far as it goes.

        True if there was anything to do. Cancelling this coroutine (a shutdown)
        hands the run back to the queue at once instead of waiting for the lease.
        """
        recovered = await self.recover_expired()
        w = self._writer
        async with w.storage.unit_of_work() as uow:
            run = await uow.runs.claim_next(w.worker_id, w.clock(), self._lease)
            await uow.commit()
        if run is None:
            return recovered > 0
        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        try:
            await self._execute(run.id)
        except asyncio.CancelledError:
            await self._release(run.id)
            raise
        finally:
            heartbeat.cancel()
        return True

    async def recover_expired(self) -> int:
        """Resume, fail or cancel runs whose worker stopped renewing its lease."""
        w = self._writer
        now = w.clock()
        async with w.storage.unit_of_work() as uow:
            expired = await uow.runs.lock_expired(now, _RECOVERY_BATCH)
            for run in expired:
                trigger = recovery_for(run)
                _log.warning(
                    "run_lease_expired",
                    run_id=str(run.id),
                    worker=run.lease_owner,
                    action=trigger.value,
                )
                if trigger is RunTrigger.REQUEUE:
                    await uow.runs.save(run.requeued(now))
                else:
                    error = RUN_LOST_MESSAGE if trigger is RunTrigger.FAIL else None
                    await w.log.finish(uow, run, trigger, error)
            await uow.commit()
        for run in expired:
            await w.signals.publish(run_channel(run.id))
        if expired:
            await w.signals.publish(QUEUE_CHANNEL)
        return len(expired)

    async def _execute(self, run_id: UUID) -> None:
        try:
            await self._execute_stages(run_id)
        except KeyDestroyedError:
            # The project was deleted while its run was going: nothing about it
            # can be written again, so the run simply ends.
            _log.info("run_project_deleted", run_id=str(run_id))
            await self._finish(run_id, RunTrigger.CANCEL, None)

    async def _execute_stages(self, run_id: UUID) -> None:
        while True:
            run = await self._held(run_id)
            if run is None:
                return
            stage_id = run.current_stage
            if run.cancel_requested_at is not None:
                await self._finish(run_id, RunTrigger.CANCEL, None)
                return
            if stage_id is None:
                await self._finish(run_id, RunTrigger.FINISH, None)
                return
            stage = self._stages.get(stage_id)
            if stage is None:
                await self._finish(run_id, RunTrigger.FAIL, stage_failed_message(stage_id))
                return
            if run.stage_started_at is None and not await self._start_stage(run_id, stage_id):
                return
            try:
                summary = await stage.run(_Context(self._writer, run, stage_id))
            except _Paused:
                return
            except _LeaseLost:
                _log.warning("run_lease_lost", run_id=str(run_id), worker=self.worker_id)
                return
            except _Cancelled:
                await self._finish(run_id, RunTrigger.CANCEL, None)
                return
            except KeyDestroyedError:
                raise
            except StageFailedError as exc:
                message = str(exc) or stage_failed_message(stage_id)
                await self._finish(run_id, RunTrigger.FAIL, message)
                return
            except Exception:
                # Members see a generic message; the details stay in the
                # deployment's own logs, where only the operator reads them.
                _log.exception("stage_failed", run_id=str(run_id), stage=stage_id)
                await self._finish(run_id, RunTrigger.FAIL, stage_failed_message(stage_id))
                return
            if not await self._finish_stage(run_id, stage_id, summary):
                return

    async def _held(self, run_id: UUID) -> Run | None:
        """The run, if it is still running under this worker's lease."""
        async with self._writer.storage.unit_of_work() as uow:
            run = await uow.runs.get(run_id)
        if (
            run is None
            or run.state is not RunState.RUNNING
            or run.lease_owner != self.worker_id
        ):
            return None
        return run

    async def _start_stage(self, run_id: UUID, stage_id: str) -> bool:
        w = self._writer
        now = w.clock()
        async with w.storage.unit_of_work() as uow:
            run = await uow.runs.lock(run_id)
            if run is None:
                return False
            payload = stage_started_payload(stage_id, run.position, run.total, now)
            if not await w.append(uow, run, EventName.STAGE_STARTED, payload):
                return False
            await uow.runs.save(run.stage_started(now))
            await uow.commit()
        await w.signals.publish(run_channel(run_id))
        return True

    async def _finish_stage(
        self, run_id: UUID, stage_id: str, summary: Mapping[str, JsonValue]
    ) -> bool:
        w = self._writer
        now = w.clock()
        async with w.storage.unit_of_work() as uow:
            run = await uow.runs.lock(run_id)
            if run is None or run.current_stage != stage_id:
                return False
            seconds = round((now - (run.stage_started_at or now)).total_seconds(), 3)
            payload = stage_finished_payload(stage_id, seconds, summary)
            if not await w.append(uow, run, EventName.STAGE_FINISHED, payload):
                return False
            await uow.runs.save(run.stage_finished())
            await uow.commit()
        await w.signals.publish(run_channel(run_id))
        return True

    async def _finish(self, run_id: UUID, trigger: RunTrigger, error: str | None) -> None:
        w = self._writer
        async with w.storage.unit_of_work() as uow:
            run = await uow.runs.lock(run_id)
            if run is None:
                return
            if await w.log.finish(uow, run, trigger, error, lease_owner=w.worker_id) is None:
                return
            await uow.commit()
        await w.signals.publish(run_channel(run_id))

    async def _release(self, run_id: UUID) -> None:
        w = self._writer
        async with w.storage.unit_of_work() as uow:
            run = await uow.runs.lock(run_id)
            if run is None or run.state is not RunState.RUNNING:
                return
            if run.lease_owner != w.worker_id:
                return
            await uow.runs.save(run.released(w.clock()))
            await uow.commit()
        await w.signals.publish(QUEUE_CHANNEL)

    async def _heartbeat(self, run_id: UUID) -> None:
        w = self._writer
        # At least three renewals per lease, and at least one per poll interval,
        # so a long stage still shows the process alive to its health check.
        interval = min(self._lease.total_seconds() / 3, self._poll_interval.total_seconds())
        while True:
            await asyncio.sleep(interval)
            self._on_alive()
            try:
                async with w.storage.unit_of_work() as uow:
                    renewed = await uow.runs.renew_lease(
                        run_id, w.worker_id, w.clock() + self._lease
                    )
                    await uow.commit()
            except StorageUnavailableError:
                _log.warning("run_lease_renewal_failed", run_id=str(run_id))
                continue
            if not renewed:
                return

    @staticmethod
    def _worked(work: asyncio.Task[bool]) -> bool:
        try:
            return work.result()
        except StorageUnavailableError:
            _log.warning("worker_storage_unavailable")
        except Exception:
            _log.exception("worker_turn_failed")
        return False

    async def _idle(self, stop: asyncio.Event) -> None:
        """Wait for a run to be queued, the poll interval, or `stop`, whichever is first."""
        signalled = asyncio.create_task(
            self._writer.signals.wait(QUEUE_CHANNEL, self._poll_interval.total_seconds())
        )
        stopping = asyncio.create_task(stop.wait())
        await asyncio.wait({signalled, stopping}, return_when=asyncio.FIRST_COMPLETED)
        for task in (signalled, stopping):
            task.cancel()


class _Context:
    """The `StageContext` one execution of one stage gets."""

    def __init__(self, writer: _Writer, run: Run, stage: str) -> None:
        self._w = writer
        self._run = run
        self._stage = stage

    @property
    def run_id(self) -> UUID:
        return self._run.id

    @property
    def review_id(self) -> UUID:
        return self._run.review_id

    @property
    def project_id(self) -> UUID:
        return self._run.project_id

    @property
    def stage(self) -> str:
        return self._stage

    async def progress(
        self,
        done: int,
        total: int,
        item: str | None = None,
        *,
        screened: Mapping[str, JsonValue] | None = None,
    ) -> None:
        payload = stage_progress_payload(
            self._stage, done, total, item, self._w.clock(), screened
        )
        await self._emit(EventName.STAGE_PROGRESS, payload)

    async def agent_step(self, step: Mapping[str, JsonValue]) -> None:
        await self._emit(EventName.AGENT_STEP, agent_step_payload(step))

    async def require_gate(
        self, kind: GateKind, payload: Mapping[str, JsonValue]
    ) -> GateResolution:
        w = self._w
        async with w.storage.unit_of_work() as uow:
            gate = await uow.gates.find(self._run.id, self._stage, kind)
            if gate is not None and gate.is_resolved:
                return await self._resolution(uow, gate)
            run = await uow.runs.lock(self._run.id)
            if run is None:
                raise _LeaseLost
            if gate is None:
                gate = await self._new_gate(uow, run, kind, payload)
            required = gate_required_payload(gate.id, kind, payload)
            if not await w.append(uow, run, EventName.GATE_REQUIRED, required):
                raise _LeaseLost
            await uow.runs.save(run.paused())
            await w.log.move_review(uow, run.review_id, RunTrigger.PAUSE)
            await uow.commit()
        await w.signals.publish(run_channel(self._run.id))
        raise _Paused

    async def raise_if_cancelled(self) -> None:
        async with self._w.storage.unit_of_work() as uow:
            run = await uow.runs.get(self._run.id)
        if run is None or run.state is not RunState.RUNNING:
            raise _LeaseLost
        if run.lease_owner != self._w.worker_id:
            raise _LeaseLost
        if run.cancel_requested_at is not None:
            raise _Cancelled

    async def _emit(self, name: EventName, payload: Mapping[str, JsonValue]) -> None:
        async with self._w.storage.unit_of_work() as uow:
            if not await self._w.append(uow, self._run, name, payload):
                raise _LeaseLost
            await uow.commit()
        await self._w.signals.publish(run_channel(self._run.id))

    async def _new_gate(
        self, uow: UnitOfWork, run: Run, kind: GateKind, payload: Mapping[str, JsonValue]
    ) -> Gate:
        gate_id = uuid.uuid4()
        gate = Gate(
            id=gate_id,
            run_id=run.id,
            project_id=run.project_id,
            stage=self._stage,
            kind=kind,
            payload_sealed=await self._w.encryption.encrypt(
                uow,
                gate_payload_context(run.project_id, gate_id),
                to_json(payload),
            ),
            created_at=self._w.clock(),
        )
        await uow.gates.add(gate)
        return gate

    async def _resolution(self, uow: UnitOfWork, gate: Gate) -> GateResolution:
        if gate.decision is None or gate.resolved_by is None:
            raise ValueError(f"gate {gate.id} is not resolved")
        revisions = None
        if gate.revisions_sealed is not None:
            revisions = from_json(
                await self._w.encryption.decrypt(
                    uow,
                    gate_revisions_context(gate.project_id, gate.id),
                    gate.revisions_sealed,
                )
            )
        return GateResolution(gate.decision, revisions, gate.resolved_by)
