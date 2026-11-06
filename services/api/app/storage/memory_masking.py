"""In-memory storage for project masking rules and papers' maskings.

Kept beside `memory.py`, like the plan tables: `memory.py` holds one
`MaskingState` and copies it with the rest, so these writes commit and roll
back with everything else in the unit of work.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.masking import MaskingRuleRecord, PaperMask


class MaskingState:
    def __init__(self) -> None:
        self.rules: dict[UUID, MaskingRuleRecord] = {}
        self.paper_masks: dict[UUID, PaperMask] = {}

    def copy(self) -> MaskingState:
        clone = MaskingState()
        clone.rules = dict(self.rules)
        clone.paper_masks = dict(self.paper_masks)
        return clone


class MemoryMaskingRuleRepository:
    def __init__(self, state: MaskingState) -> None:
        self._state = state

    async def get(self, rule_id: UUID) -> MaskingRuleRecord | None:
        return self._state.rules.get(rule_id)

    async def add(self, rule: MaskingRuleRecord, /) -> None:
        if rule.id in self._state.rules:
            raise ValueError(f"masking rule {rule.id} already exists")
        self._state.rules[rule.id] = rule

    async def list_for_project(self, project_id: UUID) -> list[MaskingRuleRecord]:
        rules = [r for r in self._state.rules.values() if r.project_id == project_id]
        return sorted(rules, key=lambda r: (r.created_at, r.id))

    async def delete(self, rule_id: UUID) -> bool:
        return self._state.rules.pop(rule_id, None) is not None


class MemoryPaperMaskRepository:
    def __init__(self, state: MaskingState) -> None:
        self._state = state

    async def get(self, paper_id: UUID) -> PaperMask | None:
        return self._state.paper_masks.get(paper_id)

    async def put(self, mask: PaperMask, /, *, replaces: tuple[str, ...] | None) -> bool:
        stored = self._state.paper_masks.get(mask.paper_id)
        if (None if stored is None else stored.masked_with) != replaces:
            return False
        self._state.paper_masks[mask.paper_id] = mask
        return True
