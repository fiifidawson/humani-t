"""Europe PMC: life-sciences literature, including PubMed and preprints.

API facts (live API, checked 2026-11-03): an unknown field name silently matches
nothing, so field codes are fixed here, never built from input. Some errors arrive as
HTTP 200 with `errCode` in the body. `pageSize` is at most 1000. `pubYear` is a string,
`isOpenAccess` is "Y" or "N", and `abstractText` carries JATS/HTML markup. The rate
limit, 10 requests per second per IP, is stated on the support forum only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.domain.sources import PaperSourceUnavailableError
from app.net.base import FetchRequest, HttpFetcher
from app.net.pacing import Pacer
from app.search._remote import bare_doi, fetch_paced, phrase, strip_markup
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "europepmc"
HOST = "www.ebi.ac.uk"
_SEARCH_URL = f"https://{HOST}/europepmc/webservices/rest/search"
_MAX_PAGE_SIZE = 1000


def pacer_for() -> Pacer:
    return Pacer(0.1)


def compile_europepmc_query(query: BooleanQuery) -> str:
    """The exact string sent as `query`. Pure, so it can be recorded."""
    themes = (
        " OR ".join(f"TITLE_ABS:{phrase(term)}" for term in theme.terms)
        for theme in query.themes
    )
    return " AND ".join(f"({clause})" for clause in themes)


class EuropePmcProvider:
    def __init__(self, fetcher: HttpFetcher, *, pacer: Pacer | None = None) -> None:
        self._fetcher = fetcher
        self._pacer = pacer or pacer_for()

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        params = (
            ("query", compile_europepmc_query(query)),
            ("format", "json"),
            ("resultType", "core"),
            ("pageSize", str(min(limit, _MAX_PAGE_SIZE))),
            ("cursorMark", "*"),
        )
        response = await fetch_paced(
            SOURCE_ID, self._fetcher, self._pacer, FetchRequest(_SEARCH_URL, params=params)
        )
        if not response.is_success:
            raise PaperSourceUnavailableError(SOURCE_ID, f"HTTP {response.status}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaperSourceUnavailableError(SOURCE_ID, "the response is not JSON") from exc
        if not isinstance(payload, dict):
            raise PaperSourceUnavailableError(SOURCE_ID, "the response is not an object")
        if "errMsg" in payload:
            raise PaperSourceUnavailableError(SOURCE_ID, str(payload["errMsg"]))
        results = (payload.get("resultList") or {}).get("result")
        if not isinstance(results, list):
            raise PaperSourceUnavailableError(SOURCE_ID, "the response has no result list")
        records = (_to_record(r) for r in results if isinstance(r, dict))
        return tuple(r for r in records if r is not None)[:limit]

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        # Full text arrives with PDF retrieval and extraction (build order step 10).
        return None


def _to_record(result: Mapping[str, Any]) -> PaperRecord | None:
    """None for a result without a title or id: there is nothing to screen or cite."""
    title = strip_markup(result.get("title"))
    source, result_id = result.get("source"), result.get("id")
    if title is None or not source or not result_id:
        return None
    year = str(result.get("pubYear") or "")
    return PaperRecord(
        source_id=SOURCE_ID,
        # Europe PMC ids are unique only within their source (MED, PMC, PPR…).
        external_id=f"{source}:{result_id}",
        title=title,
        year=int(year) if year.isascii() and year.isdigit() else None,
        doi=bare_doi(result.get("doi")),
        is_open_access=result.get("isOpenAccess") == "Y",
        abstract=strip_markup(result.get("abstractText")),
        url=f"https://europepmc.org/article/{source}/{result_id}",
    )
