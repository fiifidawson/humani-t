"""PostgreSQL repository for evaluations.

The evaluations table is also the queue of evaluations waiting for a worker. Workers claim
with `SELECT … FOR UPDATE SKIP LOCKED`, as for questions (ADR-0003), and every change is
version-checked, so two workers never judge one evaluation.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.errors import ConcurrentUpdateError
from app.domain.evaluations import MAX_ATTEMPTS, EvaluationRecord, EvaluationStatus
from app.storage.postgres.tables import evaluations

_RUNNING = EvaluationStatus.RUNNING.value


def _to_evaluation(row: Row[Any]) -> EvaluationRecord:
    return EvaluationRecord(
        id=row.id,
        project_id=row.project_id,
        review_id=row.review_id,
        created_by=row.created_by,
        created_at=row.created_at,
        judge_model=row.judge_model,
        question_count=row.question_count,
        system_count=row.system_count,
        status=EvaluationStatus(row.status),
        setup_sealed=row.setup_sealed,
        results_sealed=row.results_sealed,
        answered_count=row.answered_count,
        writer_model=row.writer_model,
        attempts=row.attempts,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        finished_at=row.finished_at,
        error=row.error,
        version=row.version,
    )


def _changing(e: EvaluationRecord) -> dict[str, Any]:
    return {
        "status": e.status.value,
        "results_sealed": e.results_sealed,
        "answered_count": e.answered_count,
        "writer_model": e.writer_model,
        "attempts": e.attempts,
        "lease_owner": e.lease_owner,
        "lease_expires_at": e.lease_expires_at,
        "finished_at": e.finished_at,
        "error": e.error,
    }


class PostgresEvaluationRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add(self, evaluation: EvaluationRecord, /) -> None:
        await self._connection.execute(
            insert(evaluations).values(
                id=evaluation.id,
                project_id=evaluation.project_id,
                review_id=evaluation.review_id,
                created_by=evaluation.created_by,
                created_at=evaluation.created_at,
                judge_model=evaluation.judge_model,
                question_count=evaluation.question_count,
                system_count=evaluation.system_count,
                setup_sealed=evaluation.setup_sealed,
                version=evaluation.version,
                **_changing(evaluation),
            )
        )

    async def _one(self, statement: Any) -> EvaluationRecord | None:
        row = (await self._connection.execute(statement)).one_or_none()
        return None if row is None else _to_evaluation(row)

    async def get(self, evaluation_id: UUID) -> EvaluationRecord | None:
        return await self._one(select(evaluations).where(evaluations.c.id == evaluation_id))

    async def list_for_projects(self, project_ids: Collection[UUID]) -> list[EvaluationRecord]:
        if not project_ids:
            return []
        result = await self._connection.execute(
            select(evaluations)
            .where(evaluations.c.project_id.in_(list(project_ids)))
            .order_by(evaluations.c.created_at.desc(), evaluations.c.id.desc())
        )
        return [_to_evaluation(row) for row in result]

    async def save(self, evaluation: EvaluationRecord, /) -> EvaluationRecord:
        result = await self._connection.execute(
            update(evaluations)
            .where(
                evaluations.c.id == evaluation.id,
                evaluations.c.version == evaluation.version,
            )
            .values(version=evaluation.version + 1, **_changing(evaluation))
            .returning(evaluations)
        )
        row = result.one_or_none()
        if row is None:
            raise ConcurrentUpdateError(f"evaluation {evaluation.id} changed concurrently")
        return _to_evaluation(row)

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> EvaluationRecord | None:
        lapsed = and_(
            evaluations.c.status == _RUNNING,
            evaluations.c.lease_expires_at < now,
            evaluations.c.attempts < MAX_ATTEMPTS,
        )
        oldest = await self._one(
            select(evaluations)
            .where(or_(evaluations.c.status == EvaluationStatus.QUEUED.value, lapsed))
            .order_by(evaluations.c.created_at, evaluations.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if oldest is None:
            return None
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, evaluation_id: UUID, worker_id: str, until: datetime) -> bool:
        result = await self._connection.execute(
            update(evaluations)
            .where(
                evaluations.c.id == evaluation_id,
                evaluations.c.status == _RUNNING,
                evaluations.c.lease_owner == worker_id,
            )
            .values(lease_expires_at=until, version=evaluations.c.version + 1)
        )
        return result.rowcount == 1

    async def lock_exhausted(self, now: datetime, limit: int) -> list[EvaluationRecord]:
        result = await self._connection.execute(
            select(evaluations)
            .where(
                evaluations.c.status == _RUNNING,
                evaluations.c.lease_expires_at < now,
                evaluations.c.attempts >= MAX_ATTEMPTS,
            )
            .order_by(evaluations.c.created_at, evaluations.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_to_evaluation(row) for row in result]
