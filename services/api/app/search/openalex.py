"""OpenAlex: the works index, searched over title and abstract.

API facts this relies on (help.openalex.org, checked 2026-11-03):

- `search.title_and_abstract` accepts upper-case AND/OR/NOT, quoted phrases
  and parentheses. Phrases are stemmed; that is OpenAlex's matching, not ours.
- `per_page` is at most 100. Basic paging stops at 10,000 results.
- An API key (sent as a bearer token by the fetcher, never in the URL) raises the
  daily budget and the rate limit. `mailto` no longer buys a "polite pool"; it is
  still sent when configured, as a contact address OpenAlex can use.
- Requests over about 4 KB of URL are refused, so a very large vocabulary fails
  here as `PaperSourceUnavailableError` rather than being silently truncated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.domain.sources import PaperSourceUnavailableError
from app.net.base import FetchRequest, HttpFetcher
from app.net.pacing import Pacer
from app.search._remote import bare_doi, fetch_paced, phrase, tidy
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "openalex"
HOST = "api.openalex.org"
_WORKS_URL = f"https://{HOST}/works"
_PAGE_SIZE = 100
_MAX_RESULTS = 10_000  # basic paging stops here; cursor paging needs an API key
_FIELDS = (
    "id,doi,display_name,publication_year,open_access,abstract_inverted_index,primary_location"
)
_ID_PREFIX = "https://openalex.org/"


def pacer_for(*, has_api_key: bool) -> Pacer:
    """Searches with over five Boolean operators: 1/s without a key, 5/s with one."""
    return Pacer(0.2 if has_api_key else 1.0)


def compile_openalex_query(query: BooleanQuery) -> str:
    """The exact string sent as `search.title_and_abstract`. Pure, so it can be recorded."""
    themes = (" OR ".join(phrase(term) for term in theme.terms) for theme in query.themes)
    return " AND ".join(f"({clause})" for clause in themes)


class OpenAlexProvider:
    def __init__(
        self, fetcher: HttpFetcher, *, mailto: str | None = None, pacer: Pacer | None = None
    ) -> None:
        self._fetcher = fetcher
        self._mailto = mailto
        self._pacer = pacer or pacer_for(has_api_key=False)

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        compiled = compile_openalex_query(query)
        per_page = min(limit, _PAGE_SIZE)
        records: list[PaperRecord] = []
        page = 1
        while len(records) < limit:
            works = await self._page(compiled, per_page, page)
            records.extend(r for r in map(_to_record, works) if r is not None)
            if len(works) < per_page or page * per_page >= _MAX_RESULTS:
                break
            page += 1
        return tuple(records[:limit])

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        # Full text arrives with PDF retrieval and extraction (build order step 10).
        return None

    async def _page(self, compiled: str, per_page: int, page: int) -> list[Mapping[str, Any]]:
        params = [
            ("search.title_and_abstract", compiled),
            ("per_page", str(per_page)),
            ("page", str(page)),
            ("select", _FIELDS),
        ]
        if self._mailto:
            params.append(("mailto", self._mailto))
        response = await fetch_paced(
            SOURCE_ID,
            self._fetcher,
            self._pacer,
            FetchRequest(_WORKS_URL, params=tuple(params)),
        )
        if not response.is_success:
            raise PaperSourceUnavailableError(SOURCE_ID, f"HTTP {response.status}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaperSourceUnavailableError(SOURCE_ID, "the response is not JSON") from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise PaperSourceUnavailableError(SOURCE_ID, "the response has no results list")
        return [w for w in results if isinstance(w, dict)]


def _to_record(work: Mapping[str, Any]) -> PaperRecord | None:
    """None for a work OpenAlex lists without a title: there is nothing to screen."""
    title = tidy(work.get("display_name"))
    work_id = str(work.get("id") or "").removeprefix(_ID_PREFIX)
    if title is None or not work_id:
        return None
    open_access = work.get("open_access") or {}
    location = work.get("primary_location") or {}
    year = work.get("publication_year")
    return PaperRecord(
        source_id=SOURCE_ID,
        external_id=work_id,
        title=title,
        year=year if isinstance(year, int) else None,
        doi=bare_doi(work.get("doi")),
        is_open_access=bool(open_access.get("is_oa")),
        abstract=_abstract(work.get("abstract_inverted_index")),
        url=location.get("landing_page_url") or f"{_ID_PREFIX}{work_id}",
    )


def _abstract(inverted_index: object) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]}; put the words back in order."""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    positioned: list[tuple[int, str]] = [
        (position, str(word))
        for word, positions in inverted_index.items()
        if isinstance(positions, list)
        for position in positions
        if isinstance(position, int)
    ]
    return tidy(" ".join(word for _, word in sorted(positioned)))
