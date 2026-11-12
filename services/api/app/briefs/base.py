"""The seams only the decision brief uses: passage retrieval and cited-paper details.

`Retriever` is BM25 today (ADR-0007); a vector store can replace it without the
loop changing. Passages and masking come through the shared seams.
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
