"""In-memory seen and saved marks, brief reads and Needs you dismissals.

Kept beside `memory.py`, as `memory_label_rules.py` is: `memory.py` holds one `MarkState`
and copies it with the rest, so a mark commits and rolls back with everything else.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.history import Dismissal
from app.domain.marks import BriefRead, PaperMark, PaperMarkKind


class MarkState:
    def __init__(self) -> None:
        self.paper_marks: dict[tuple[UUID, UUID, PaperMarkKind], PaperMark] = {}
        self.brief_reads: dict[tuple[UUID, UUID], BriefRead] = {}
        self.dismissals: dict[tuple[UUID, str], Dismissal] = {}

    def copy(self) -> MarkState:
        clone = MarkState()
        clone.paper_marks = dict(self.paper_marks)
        clone.brief_reads = dict(self.brief_reads)
        clone.dismissals = dict(self.dismissals)
        return clone


class MemoryMarkRepository:
    def __init__(self, state: MarkState) -> None:
        self._state = state

    async def add_paper_mark(self, mark: PaperMark, /) -> bool:
        key = (mark.user_id, mark.paper_id, mark.kind)
        if key in self._state.paper_marks:
            return False
        self._state.paper_marks[key] = mark
        return True

    async def remove_paper_mark(
        self, user_id: UUID, paper_id: UUID, kind: PaperMarkKind
    ) -> bool:
        return self._state.paper_marks.pop((user_id, paper_id, kind), None) is not None

    async def list_paper_marks(self, user_id: UUID) -> list[PaperMark]:
        mine = [m for m in self._state.paper_marks.values() if m.user_id == user_id]
        return sorted(mine, key=lambda m: m.marked_at)

    async def add_brief_read(self, read: BriefRead, /) -> bool:
        key = (read.user_id, read.brief_id)
        if key in self._state.brief_reads:
            return False
        self._state.brief_reads[key] = read
        return True

    async def list_brief_reads(self, user_id: UUID) -> list[BriefRead]:
        return [r for r in self._state.brief_reads.values() if r.user_id == user_id]


class MemoryDismissalRepository:
    def __init__(self, state: MarkState) -> None:
        self._state = state

    async def add(self, dismissal: Dismissal, /) -> bool:
        key = (dismissal.user_id, dismissal.item_key)
        if key in self._state.dismissals:
            return False
        self._state.dismissals[key] = dismissal
        return True

    async def list_for_user(self, user_id: UUID) -> list[Dismissal]:
        return [d for d in self._state.dismissals.values() if d.user_id == user_id]
