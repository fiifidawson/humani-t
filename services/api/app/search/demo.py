"""Made-up papers for development: a paper source that needs no internet.

Production refuses `demo` in `paper_sources`, and every paper is labelled as a demo, so
none can pass for a real record. Some lack an abstract or carry made-up contact details,
so skipping and masking have something to show. Tests use `FakePaperSearchProvider`.
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
