"""In-memory evaluations.

Kept beside `memory.py`, as `memory_questions.py` is: `memory.py` holds one
`EvaluationState` and copies it with the rest, so evaluations commit and roll back
with everything else in the unit of work.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.errors import ConcurrentUpdateError
from app.domain.evaluations import MAX_ATTEMPTS, EvaluationRecord, EvaluationStatus


class EvaluationState:
    def __init__(self) -> None:
        self.evaluations: dict[UUID, EvaluationRecord] = {}

    def copy(self) -> EvaluationState:
        clone = EvaluationState()
        clone.evaluations = dict(self.evaluations)
        return clone


class MemoryEvaluationRepository:
    def __init__(self, state: EvaluationState) -> None:
        self._state = state

    async def add(self, evaluation: EvaluationRecord, /) -> None:
        if evaluation.id in self._state.evaluations:
            raise ValueError(f"evaluation {evaluation.id} already exists")
        self._state.evaluations[evaluation.id] = evaluation

    async def get(self, evaluation_id: UUID) -> EvaluationRecord | None:
        return self._state.evaluations.get(evaluation_id)

    async def list_for_projects(self, project_ids: Collection[UUID]) -> list[EvaluationRecord]:
        found = [e for e in self._state.evaluations.values() if e.project_id in project_ids]
        return sorted(found, key=lambda e: (e.created_at, e.id), reverse=True)

    async def save(self, evaluation: EvaluationRecord, /) -> EvaluationRecord:
        stored = self._state.evaluations.get(evaluation.id)
        if stored is None:
            raise ValueError(f"evaluation {evaluation.id} does not exist")
        if stored.version != evaluation.version:
            raise ConcurrentUpdateError(f"evaluation {evaluation.id} changed concurrently")
        saved = replace(evaluation, version=evaluation.version + 1)
        self._state.evaluations[evaluation.id] = saved
        return saved

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> EvaluationRecord | None:
        waiting = [e for e in self._state.evaluations.values() if e.can_be_claimed(now)]
        if not waiting:
            return None
        oldest = min(waiting, key=lambda e: (e.created_at, e.id))
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, evaluation_id: UUID, worker_id: str, until: datetime) -> bool:
        stored = self._state.evaluations.get(evaluation_id)
        if (
            stored is None
            or stored.status is not EvaluationStatus.RUNNING
            or stored.lease_owner != worker_id
        ):
            return False
        await self.save(replace(stored, lease_expires_at=until))
        return True

    async def lock_exhausted(self, now: datetime, limit: int) -> list[EvaluationRecord]:
        stuck = [
            e
            for e in self._state.evaluations.values()
            if e.has_lapsed(now) and e.attempts >= MAX_ATTEMPTS
        ]
        return sorted(stuck, key=lambda e: (e.created_at, e.id))[:limit]
