"""Made-up papers for development: a paper source that needs no internet.

Chosen only by naming `demo` in the operator setting `paper_sources`, which
production refuses. It writes papers about whatever the query asks for, so any
review finds something, and labels every one as a demo paper so none can pass
for a real record. Some have no abstract, so screening shows skipped papers;
some carry a made-up email address and phone number, so masking has something
to find; some have a DOI and no link, as many real records do.

Not for tests of the search seam: `FakePaperSearchProvider` evaluates queries
against a fixed corpus instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "demo"

_PAPERS = 12
# 10.5555 is the prefix reserved for examples and tests: it never names a real paper.
_DOI_PREFIX = "10.5555/humani-t-demo"


class DemoPaperSearchProvider:
    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        terms = [theme.terms[0] for theme in query.themes] or ["humanitarian response"]
        return tuple(_paper(n, terms) for n in range(1, min(limit, _PAPERS) + 1))

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        return None


def compile_demo_query(query: BooleanQuery) -> str:
    return query.render()


def _paper(n: int, terms: Sequence[str]) -> PaperRecord:
    subject = " and ".join(terms[(n + i) % len(terms)] for i in range(min(2, len(terms))))
    abstract = None
    if n % 5 != 0:
        abstract = (
            f"Demo abstract {n}, made up for development. A study of {subject} among "
            "people affected by crisis, with outcomes measured after six months."
        )
        if n % 3 == 0:
            abstract += " Contact the field team at demo.team@example.org or +44 20 7946 0000."
    return PaperRecord(
        source_id=SOURCE_ID,
        external_id=f"demo-{n}",
        title=f"Demo paper {n}: {subject}",
        year=2015 + n % 10,
        doi=f"{_DOI_PREFIX}.{n}" if n % 2 == 0 else None,
        is_open_access=n % 2 == 0,
        abstract=abstract,
        url=None if n % 2 == 0 else f"https://example.org/humani-t-demo/{n}",
    )
