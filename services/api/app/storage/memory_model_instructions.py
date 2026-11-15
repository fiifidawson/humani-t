"""In-memory model instruction versions.

Kept beside `memory.py`, as `memory_label_rules.py` is: `memory.py` holds one
`ModelInstructionsState` and copies it with the rest, so a version commits and rolls back
with everything else.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.model_instructions import StoredModelInstructions


class ModelInstructionsState:
    def __init__(self) -> None:
        self.versions: dict[tuple[UUID, int], StoredModelInstructions] = {}

    def copy(self) -> ModelInstructionsState:
        clone = ModelInstructionsState()
        clone.versions = dict(self.versions)
        return clone


class MemoryModelInstructionsRepository:
    def __init__(self, state: ModelInstructionsState) -> None:
        self._state = state

    async def add(self, version: StoredModelInstructions, /) -> bool:
        key = (version.project_id, version.version)
        if key in self._state.versions:
            return False
        self._state.versions[key] = version
        return True

    async def latest(self, project_id: UUID) -> StoredModelInstructions | None:
        return max(self._of(project_id), key=lambda v: v.version, default=None)

    async def in_force(self, project_id: UUID, at: datetime) -> StoredModelInstructions | None:
        saved = (v for v in self._of(project_id) if v.created_at <= at)
        return max(saved, key=lambda v: v.version, default=None)

    async def list_for_project(self, project_id: UUID) -> list[StoredModelInstructions]:
        return sorted(self._of(project_id), key=lambda v: v.version)

    def _of(self, project_id: UUID) -> list[StoredModelInstructions]:
        return [v for v in self._state.versions.values() if v.project_id == project_id]
