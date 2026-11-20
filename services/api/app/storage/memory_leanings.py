"""In-memory leanings ("Your view first").

Kept beside `memory.py`, as `memory_model_instructions.py` is: `memory.py` holds one
`LeaningState` and copies it with the rest, so an answer commits and rolls back with
everything else.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.leanings import StoredLeaning


class LeaningState:
    def __init__(self) -> None:
        self.leanings: dict[tuple[UUID, UUID], StoredLeaning] = {}

    def copy(self) -> LeaningState:
        clone = LeaningState()
        clone.leanings = dict(self.leanings)
        return clone


class MemoryLeaningRepository:
    def __init__(self, state: LeaningState) -> None:
        self._state = state

    async def add(self, leaning: StoredLeaning, /) -> bool:
        key = (leaning.run_id, leaning.user_id)
        if key in self._state.leanings:
            return False
        self._state.leanings[key] = leaning
        return True

    async def get(self, run_id: UUID, user_id: UUID) -> StoredLeaning | None:
        return self._state.leanings.get((run_id, user_id))
