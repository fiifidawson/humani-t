"""Running a search: compile the vocabulary, ask a provider, keep the provenance."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.paper import PaperRecord, deduplicate
from app.domain.query import BooleanQuery, compile_vocabulary
from app.domain.vocabulary import Vocabulary
from app.search.base import DEFAULT_LIMIT, PaperSearchProvider


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Results bound to the query that produced them.

    Returned together on purpose: a result set separated from its query cannot be audited.
    """

    source_id: str
    query: BooleanQuery
    records: tuple[PaperRecord, ...]

    @property
    def total(self) -> int:
        return len(self.records)


class SearchService:
    """Depends on the Protocol, never on a concrete provider.

    That is what lets tests run it against the fake.
    """

    def __init__(self, provider: PaperSearchProvider) -> None:
        self._provider = provider

    async def run(self, vocabulary: Vocabulary, limit: int = DEFAULT_LIMIT) -> SearchOutcome:
        query = compile_vocabulary(vocabulary)
        found = await self._provider.search(query, limit=limit)
        return SearchOutcome(
            source_id=self._provider.source_id,
            query=query,
            # Almost a no-op with one source, but this is where a second one merges, and a
            # missing dedup step is invisible until counts are already wrong.
            records=deduplicate(found),
        )
