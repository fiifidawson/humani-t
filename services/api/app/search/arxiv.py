"""arXiv: preprints, searched over title and abstract through the Atom API.

API facts (info.arxiv.org/help/api, checked 2026-11-03): no combined title-and-abstract
field, so each term is asked for under `ti:` and `abs:`. Terms of use allow one request
every three seconds, one connection at a time, across the whole deployment. Errors come
back as an Atom entry titled "Error". PDFs keep their authors' licences, so are not stored.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.domain.sources import PaperSourceUnavailableError
from app.net.base import FetchRequest, HttpFetcher
from app.net.pacing import Pacer
from app.search._remote import bare_doi, fetch_paced, phrase, tidy
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "arxiv"
HOST = "export.arxiv.org"
MIN_INTERVAL_SECONDS = 3.0
_QUERY_URL = f"https://{HOST}/api/query"
_MAX_RESULTS = 2000
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_ABS_PREFIX = ("http://arxiv.org/abs/", "https://arxiv.org/abs/")
_ERROR_PREFIX = ("http://arxiv.org/api/errors", "https://arxiv.org/api/errors")


def pacer_for() -> Pacer:
    return Pacer(MIN_INTERVAL_SECONDS)


def compile_arxiv_query(query: BooleanQuery) -> str:
    """The exact string sent as `search_query`. Pure, so it can be recorded."""
    themes = (
        " OR ".join(f"ti:{phrase(t)} OR abs:{phrase(t)}" for t in theme.terms)
        for theme in query.themes
    )
    return " AND ".join(f"({clause})" for clause in themes)


class ArxivProvider:
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
            ("search_query", compile_arxiv_query(query)),
            ("start", "0"),
            ("max_results", str(min(limit, _MAX_RESULTS))),
        )
        response = await fetch_paced(
            SOURCE_ID, self._fetcher, self._pacer, FetchRequest(_QUERY_URL, params=params)
        )
        feed = _parse_feed(response.body)
        entries = feed.findall(f"{_ATOM}entry")
        error = _error_reason(entries)
        if error is not None:
            raise PaperSourceUnavailableError(SOURCE_ID, error)
        if not response.is_success:
            raise PaperSourceUnavailableError(SOURCE_ID, f"HTTP {response.status}")
        records = (_to_record(entry) for entry in entries)
        return tuple(r for r in records if r is not None)[:limit]

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        # Full text arrives with PDF retrieval and extraction (build order step 10).
        return None


def _parse_feed(body: bytes) -> ET.Element:
    # Atom never needs a document type declaration. Refusing one outright rules
    # out entity expansion and external entities whatever the XML parser's version.
    head = body[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in body.upper():
        raise PaperSourceUnavailableError(SOURCE_ID, "the response declares a DTD")
    try:
        root = ET.fromstring(body)  # noqa: S314 — DTDs refused above; expat expands nothing else
    except ET.ParseError as exc:
        raise PaperSourceUnavailableError(
            SOURCE_ID, "the response is not an Atom feed"
        ) from exc
    if root.tag != f"{_ATOM}feed":
        raise PaperSourceUnavailableError(SOURCE_ID, "the response is not an Atom feed")
    return root


def _error_reason(entries: list[ET.Element]) -> str | None:
    if len(entries) != 1:
        return None
    entry_id = entries[0].findtext(f"{_ATOM}id") or ""
    if not entry_id.startswith(_ERROR_PREFIX):
        return None
    return tidy(entries[0].findtext(f"{_ATOM}summary")) or "arXiv reported an error"


def _to_record(entry: ET.Element) -> PaperRecord | None:
    entry_id = entry.findtext(f"{_ATOM}id") or ""
    title = tidy(entry.findtext(f"{_ATOM}title"))
    arxiv_id = next(
        (entry_id.removeprefix(p) for p in _ABS_PREFIX if entry_id.startswith(p)), None
    )
    if title is None or not arxiv_id:
        return None
    published = entry.findtext(f"{_ATOM}published") or ""
    return PaperRecord(
        source_id=SOURCE_ID,
        external_id=arxiv_id,
        title=title,
        year=int(published[:4]) if published[:4].isdigit() else None,
        doi=bare_doi(entry.findtext(f"{_ARXIV}doi")),
        # Every arXiv paper can be read free of charge; reuse depends on its licence.
        is_open_access=True,
        abstract=tidy(entry.findtext(f"{_ATOM}summary")),
        url=f"https://arxiv.org/abs/{arxiv_id}",
    )
