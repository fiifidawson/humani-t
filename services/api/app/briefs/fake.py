"""A stand-in for the evidence a run screened in, and what is known of its papers.

`FakeEvidenceSource` answers with the passages and source details it was given,
for any review and run, so a spec can write a brief without running the
discover, ingest, mask and screen stages first. No storage, no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from app.domain.briefs import SourceDetails
from app.domain.screening import Passage
from app.storage.base import UnitOfWork


class FakeEvidenceSource:
    def __init__(
        self,
        passages: Sequence[Passage] = (),
        details: Mapping[UUID, SourceDetails] | None = None,
    ) -> None:
        self._passages = tuple(passages)
        self._details = dict(details or {})
        self.asked: list[tuple[UUID, UUID]] = []

    async def included_passages(
        self, uow: UnitOfWork, review_id: UUID, run_id: UUID
    ) -> tuple[Passage, ...]:
        self.asked.append((review_id, run_id))
        return self._passages

    async def describe(
        self, uow: UnitOfWork, run_id: UUID, paper_ids: Sequence[UUID]
    ) -> dict[UUID, SourceDetails]:
        return {p: self._details[p] for p in paper_ids if p in self._details}
