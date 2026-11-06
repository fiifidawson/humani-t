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

    Returned together on purpose. A result set that has been separated from its
    query cannot be audited, and auditability is the point of the system.
    """

    source_id: str
    query: BooleanQuery
    records: tuple[PaperRecord, ...]

    @property
    def total(self) -> int:
        return len(self.records)


class SearchService:
    """Depends on the Protocol, never on a concrete provider.

    It cannot tell whether it is talking to an in-memory corpus or to OpenAlex,
    which is exactly the property that lets tests run against the fake.
    """

    def __init__(self, provider: PaperSearchProvider) -> None:
        self._provider = provider

    async def run(self, vocabulary: Vocabulary, limit: int = DEFAULT_LIMIT) -> SearchOutcome:
        query = compile_vocabulary(vocabulary)
        found = await self._provider.search(query, limit=limit)
        return SearchOutcome(
            source_id=self._provider.source_id,
            query=query,
            # Deduplicating a single source is almost a no-op today. It is here
            # because the moment a second provider is added this becomes the
            # merge point, and a missing dedup step is invisible until counts
            # are already wrong.
            records=deduplicate(found),
        )
