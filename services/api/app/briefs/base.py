"""The Protocols the decision brief is written through, beyond those it shares.

- `Retriever` searches a run's passages. Today it is BM25 in process
  (`bm25.py`, ADR-0007); the vector store named in architecture §3.6 can
  replace it without the loop changing.
- `SourceCatalogue` says what is known of a cited paper (title, year, its
  screening label and rationale), captured when the brief is written.

The passages themselves come through the pipeline's `EvidenceSource`
(`domain/screening.py`); the question is masked by the mask lane's `Sanitizer`
(`privacy/base.py`). Implementations live beside this module or in
`application/`, chosen in `api/dependencies.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from app.domain.briefs import SourceDetails
from app.domain.screening import Passage
from app.storage.base import UnitOfWork


class Retriever(Protocol):
    @property
    def retriever_id(self) -> str:
        """The method and its version, as a `RunManifest` records it (`bm25-okapi/1`)."""
        ...

    async def search(
        self, corpus: Sequence[Passage], query: str, *, limit: int
    ) -> tuple[Passage, ...]:
        """The passages of `corpus` that best answer `query`, best first, at most `limit`.

        Passages sharing nothing with the query are not returned, so the
        result may be empty. The same inputs give the same order.
        """
        ...


class SourceCatalogue(Protocol):
    async def describe(
        self, uow: UnitOfWork, run_id: UUID, paper_ids: Sequence[UUID]
    ) -> Mapping[UUID, SourceDetails]:
        """What is known of each paper in this run. A paper with nothing known is left out."""
        ...
