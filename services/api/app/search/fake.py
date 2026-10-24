"""An in-memory PaperSearchProvider. No network, no key, no rate limit.

This exists to prove the interface is right before any real source is involved.
If a fake is awkward to write against an interface, the interface is wrong — and
finding that out here costs nothing.

It genuinely evaluates the query rather than returning a fixed list, so tests
exercise the compiled Boolean logic instead of trivially passing.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "fake"

# A small corpus chosen so that AND across themes actually discriminates:
# some records match one theme only, and must not be returned.
DEFAULT_CORPUS: tuple[PaperRecord, ...] = (
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-001",
        title="Conversational agents for crisis response in humanitarian settings",
        year=2024,
        doi="10.1000/fake.001",
        is_open_access=True,
        abstract=(
            "We study chatbot deployment by humanitarian organisations during "
            "acute crisis response, with attention to transparency."
        ),
        url="https://example.org/fake-001",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-002",
        title="Interpretability of large language models in clinical triage",
        year=2023,
        doi="10.1000/fake.002",
        is_open_access=True,
        abstract=(
            "An evaluation of interpretability methods applied to a chatbot "
            "used for humanitarian triage in low-resource settings."
        ),
        url="https://example.org/fake-002",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-003",
        title="A survey of chatbot architectures",
        year=2022,
        doi=None,
        is_open_access=False,
        abstract="General architectures for conversational agents. No deployment context.",
        url="https://example.org/fake-003",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-004",
        title="Humanitarian logistics under uncertainty",
        year=2021,
        doi="10.1000/fake.004",
        is_open_access=True,
        abstract="Supply chain optimisation for humanitarian response. No language models.",
        url="https://example.org/fake-004",
    ),
)

FULLTEXT: dict[str, str] = {
    "fake-001": "Full text of the crisis response study.",
    "fake-002": "Full text of the interpretability evaluation.",
    # fake-003 and fake-004 are deliberately absent: not every paper is open.
}


def _matches(record: PaperRecord, query: BooleanQuery) -> bool:
    """AND across themes, OR within a theme — the same shape as render()."""
    haystack = record.searchable_text
    return all(
        any(term.casefold() in haystack for term in theme.terms) for theme in query.themes
    )


class FakePaperSearchProvider:
    """Satisfies PaperSearchProvider structurally, without inheriting from it."""

    def __init__(self, corpus: Sequence[PaperRecord] | None = None) -> None:
        self._corpus = tuple(corpus) if corpus is not None else DEFAULT_CORPUS
        self.calls: list[BooleanQuery] = []

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        self.calls.append(query)
        hits = [record for record in self._corpus if _matches(record, query)]
        return tuple(hits[:limit])

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        return FULLTEXT.get(record.external_id)
