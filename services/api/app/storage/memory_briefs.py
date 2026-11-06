"""In-memory decision brief storage.

Kept beside `memory.py` rather than in it, as `memory_plans.py` is: `memory.py`
holds one `BriefState` in its state and copies it with the rest, so a brief
commits and rolls back with everything else in the unit of work.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.briefs import BriefRecord


class BriefState:
    def __init__(self) -> None:
        self.briefs: dict[UUID, BriefRecord] = {}

    def copy(self) -> BriefState:
        clone = BriefState()
        clone.briefs = dict(self.briefs)
        return clone


class MemoryDecisionBriefRepository:
    def __init__(self, state: BriefState) -> None:
        self._state = state

    async def add(self, brief: BriefRecord, /) -> bool:
        if await self.for_run(brief.run_id) is not None:
            return False
        if any(
            (b.review_id, b.version) == (brief.review_id, brief.version)
            for b in self._state.briefs.values()
        ):
            raise ValueError(f"review {brief.review_id} already has version {brief.version}")
        self._state.briefs[brief.id] = brief
        return True

    async def get(self, brief_id: UUID) -> BriefRecord | None:
        return self._state.briefs.get(brief_id)

    async def for_run(self, run_id: UUID) -> BriefRecord | None:
        return next((b for b in self._state.briefs.values() if b.run_id == run_id), None)

    async def latest_for_review(self, review_id: UUID) -> BriefRecord | None:
        briefs = [b for b in self._state.briefs.values() if b.review_id == review_id]
        return max(briefs, key=lambda b: b.version, default=None)

    async def count_for_review(self, review_id: UUID) -> int:
        return sum(1 for b in self._state.briefs.values() if b.review_id == review_id)
