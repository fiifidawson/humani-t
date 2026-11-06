"""PostgreSQL repositories for reviews, runs, the run event log and gates.

The run table is also the job queue. Workers claim with `SELECT … FOR UPDATE
SKIP LOCKED`, so two workers never take the same run and never wait for each
other; see docs/adr/0003-run-queue-and-event-fan-out.md for why a table and
not a separate broker.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.errors import ConcurrentUpdateError
from app.domain.reviews import Review, ReviewStatus
from app.domain.runs import (
    EventName,
    Gate,
    GateDecision,
    GateKind,
    Run,
    RunEvent,
    RunState,
)
from app.storage.postgres.tables import gates, reviews, run_events, runs


def _to_review(row: Row[Any]) -> Review:
    return Review(
        id=row.id,
        project_id=row.project_id,
        question_sealed=row.question_sealed,
        status=ReviewStatus(row.status),
        created_at=row.created_at,
        created_by=row.created_by,
        version=row.version,
    )


class PostgresReviewRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, review_id: UUID) -> Review | None:
        row = (
            await self._connection.execute(select(reviews).where(reviews.c.id == review_id))
        ).one_or_none()
        return None if row is None else _to_review(row)

    async def add(self, review: Review, /) -> None:
        await self._connection.execute(
            insert(reviews).values(
                id=review.id,
                project_id=review.project_id,
                question_sealed=review.question_sealed,
                status=review.status.value,
                created_at=review.created_at,
                created_by=review.created_by,
                version=review.version,
            )
        )

    async def list_for_project(self, project_id: UUID) -> list[Review]:
        result = await self._connection.execute(
            select(reviews)
            .where(reviews.c.project_id == project_id)
            .order_by(reviews.c.created_at.desc(), reviews.c.id.desc())
        )
        return [_to_review(row) for row in result]

    async def save(self, review: Review, /) -> Review:
        result = await self._connection.execute(
            update(reviews)
            .where(reviews.c.id == review.id, reviews.c.version == review.version)
            .values(
                question_sealed=review.question_sealed,
                status=review.status.value,
                version=review.version + 1,
            )
            .returning(*reviews.c)
        )
        row = result.one_or_none()
        if row is None:
            if await self.get(review.id) is None:
                raise ValueError(f"review {review.id} does not exist")
            raise ConcurrentUpdateError(f"review {review.id} changed concurrently")
        return _to_review(row)


def _to_run(row: Row[Any]) -> Run:
    return Run(
        id=row.id,
        review_id=row.review_id,
        project_id=row.project_id,
        stages=tuple(row.stages),
        state=RunState(row.state),
        created_at=row.created_at,
        created_by=row.created_by,
        queued_at=row.queued_at,
        stages_finished=row.stages_finished,
        stage_started_at=row.stage_started_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested_at=row.cancel_requested_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        recoveries=row.recoveries,
        version=row.version,
    )


def _run_values(run: Run) -> dict[str, Any]:
    """Every column a save may change; the ids and creation details never do."""
    return {
        "state": run.state.value,
        "queued_at": run.queued_at,
        "stages_finished": run.stages_finished,
        "stage_started_at": run.stage_started_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cancel_requested_at": run.cancel_requested_at,
        "lease_owner": run.lease_owner,
        "lease_expires_at": run.lease_expires_at,
        "recoveries": run.recoveries,
    }


class PostgresRunRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def _one(self, query: Any) -> Run | None:
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else _to_run(row)

    async def get(self, run_id: UUID) -> Run | None:
        return await self._one(select(runs).where(runs.c.id == run_id))

    async def lock(self, run_id: UUID) -> Run | None:
        return await self._one(select(runs).where(runs.c.id == run_id).with_for_update())

    async def add(self, run: Run, /) -> None:
        await self._connection.execute(
            insert(runs).values(
                id=run.id,
                review_id=run.review_id,
                project_id=run.project_id,
                stages=list(run.stages),
                created_at=run.created_at,
                created_by=run.created_by,
                version=run.version,
                **_run_values(run),
            )
        )

    async def list_for_review(self, review_id: UUID) -> list[Run]:
        result = await self._connection.execute(
            select(runs)
            .where(runs.c.review_id == review_id)
            .order_by(runs.c.created_at.desc(), runs.c.id.desc())
        )
        return [_to_run(row) for row in result]

    async def save(self, run: Run, /) -> Run:
        saved = await self._one(
            update(runs)
            .where(runs.c.id == run.id, runs.c.version == run.version)
            .values(version=run.version + 1, **_run_values(run))
            .returning(*runs.c)
        )
        if saved is None:
            if await self.get(run.id) is None:
                raise ValueError(f"run {run.id} does not exist")
            raise ConcurrentUpdateError(f"run {run.id} changed concurrently")
        return saved

    async def claim_next(self, worker_id: str, now: datetime, lease: timedelta) -> Run | None:
        # SKIP LOCKED: a run another worker is claiming right now is passed
        # over, not waited for. The partial index ix_runs_queue serves this.
        oldest = await self._one(
            select(runs)
            .where(runs.c.state == RunState.QUEUED.value)
            .order_by(runs.c.queued_at, runs.c.created_at, runs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if oldest is None:
            return None
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, run_id: UUID, worker_id: str, until: datetime) -> bool:
        result = await self._connection.execute(
            update(runs)
            .where(
                runs.c.id == run_id,
                runs.c.state == RunState.RUNNING.value,
                runs.c.lease_owner == worker_id,
            )
            .values(lease_expires_at=until, version=runs.c.version + 1)
        )
        return result.rowcount == 1

    async def lock_expired(self, now: datetime, limit: int) -> list[Run]:
        result = await self._connection.execute(
            select(runs)
            .where(runs.c.state == RunState.RUNNING.value, runs.c.lease_expires_at < now)
            .order_by(runs.c.lease_expires_at, runs.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_to_run(row) for row in result]


class PostgresRunEventRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def next_id(self, run_id: UUID, *, lease_owner: str | None = None) -> int | None:
        # The run's row lock serialises every writer of its log, so two
        # transactions can never compute the same next id.
        run = (
            await self._connection.execute(
                select(runs.c.state, runs.c.lease_owner)
                .where(runs.c.id == run_id)
                .with_for_update()
            )
        ).one_or_none()
        if run is None or RunState(run.state) in (
            RunState.DONE,
            RunState.FAILED,
            RunState.CANCELLED,
        ):
            return None
        if lease_owner is not None and (
            run.state != RunState.RUNNING.value or run.lease_owner != lease_owner
        ):
            return None
        return await self.last_id(run_id) + 1

    async def add(self, event: RunEvent, /) -> None:
        await self._connection.execute(
            insert(run_events).values(
                run_id=event.run_id,
                id=event.id,
                name=event.name.value,
                payload_sealed=event.payload_sealed,
                occurred_at=event.occurred_at,
            )
        )

    async def list_after(self, run_id: UUID, after: int, limit: int) -> list[RunEvent]:
        result = await self._connection.execute(
            select(run_events)
            .where(run_events.c.run_id == run_id, run_events.c.id > after)
            .order_by(run_events.c.id)
            .limit(limit)
        )
        return [
            RunEvent(
                run_id=row.run_id,
                id=row.id,
                name=EventName(row.name),
                payload_sealed=row.payload_sealed,
                occurred_at=row.occurred_at,
            )
            for row in result
        ]

    async def last_id(self, run_id: UUID) -> int:
        result = await self._connection.execute(
            select(func.coalesce(func.max(run_events.c.id), 0)).where(
                run_events.c.run_id == run_id
            )
        )
        return int(result.scalar_one())


def _to_gate(row: Row[Any]) -> Gate:
    return Gate(
        id=row.id,
        run_id=row.run_id,
        project_id=row.project_id,
        stage=row.stage,
        kind=GateKind(row.kind),
        payload_sealed=row.payload_sealed,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        decision=None if row.decision is None else GateDecision(row.decision),
        revisions_sealed=row.revisions_sealed,
    )


class PostgresGateRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def _one(self, *conditions: Any) -> Gate | None:
        row = (await self._connection.execute(select(gates).where(*conditions))).one_or_none()
        return None if row is None else _to_gate(row)

    async def get(self, gate_id: UUID) -> Gate | None:
        return await self._one(gates.c.id == gate_id)

    async def find(self, run_id: UUID, stage: str, kind: GateKind) -> Gate | None:
        return await self._one(
            gates.c.run_id == run_id, gates.c.stage == stage, gates.c.kind == kind.value
        )

    async def add(self, gate: Gate, /) -> None:
        await self._connection.execute(
            insert(gates).values(
                id=gate.id,
                run_id=gate.run_id,
                project_id=gate.project_id,
                stage=gate.stage,
                kind=gate.kind.value,
                payload_sealed=gate.payload_sealed,
                created_at=gate.created_at,
                resolved_at=gate.resolved_at,
                resolved_by=gate.resolved_by,
                decision=None if gate.decision is None else gate.decision.value,
                revisions_sealed=gate.revisions_sealed,
            )
        )

    async def resolve(self, gate: Gate, /) -> bool:
        # One UPDATE with the condition inside it: Postgres row-locks the gate,
        # so a second decision waits, re-checks, and finds it already made.
        result = await self._connection.execute(
            update(gates)
            .where(gates.c.id == gate.id, gates.c.resolved_at.is_(None))
            .values(
                resolved_at=gate.resolved_at,
                resolved_by=gate.resolved_by,
                decision=None if gate.decision is None else gate.decision.value,
                revisions_sealed=gate.revisions_sealed,
            )
        )
        return result.rowcount == 1
