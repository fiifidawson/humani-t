"""In-memory label rule versions.

Kept beside `memory.py`, as `memory_briefs.py` is: `memory.py` holds one `LabelRulesState`
and copies it with the rest, so a version commits and rolls back with everything else.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.label_rules import LabelRulesVersion


class LabelRulesState:
    def __init__(self) -> None:
        self.versions: dict[tuple[UUID, int], LabelRulesVersion] = {}

    def copy(self) -> LabelRulesState:
        clone = LabelRulesState()
        clone.versions = dict(self.versions)
        return clone


class MemoryLabelRulesRepository:
    def __init__(self, state: LabelRulesState) -> None:
        self._state = state

    async def add(self, version: LabelRulesVersion, /) -> bool:
        key = (version.review_id, version.version)
        if key in self._state.versions:
            return False
        self._state.versions[key] = version
        return True

    async def latest(self, review_id: UUID) -> LabelRulesVersion | None:
        return max(
            (v for v in self._state.versions.values() if v.review_id == review_id),
            key=lambda v: v.version,
            default=None,
        )

    async def list_for_review(self, review_id: UUID) -> list[LabelRulesVersion]:
        return sorted(
            (v for v in self._state.versions.values() if v.review_id == review_id),
            key=lambda v: v.version,
        )
