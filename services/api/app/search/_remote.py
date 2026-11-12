"""What the remote paper sources share: one paced request, phrase quoting and text cleanup.

Private to `app.search`. Anything specific to one source's syntax or response
shape stays in that source's module.
"""

from __future__ import annotations

import html
import re

from app.domain.sources import PaperSourceUnavailableError
from app.net.base import FetchRequest, FetchResponse, HttpFetcher, OutboundError
from app.net.pacing import Pacer

_WHITESPACE = re.compile(r"\s+")
_MARKUP = re.compile(r"<[^>]*>")
_DOI_RESOLVER = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)


async def fetch_paced(
    source_id: str, fetcher: HttpFetcher, pacer: Pacer, request: FetchRequest
) -> FetchResponse:
    """Send one request in this source's turn; a network failure means the source is down."""
    async with pacer.turn():
        try:
            return await fetcher.fetch(request)
        except OutboundError as exc:
            raise PaperSourceUnavailableError(source_id, type(exc).__name__) from exc


def phrase(term: str) -> str:
    """A term as one quoted phrase that cannot break out of its quotes.

    An inner quote would let the rest be read as operators. Sources drop quotes when
    matching anyway, so a space in its place changes nothing.
    """
    return '"' + term.replace('"', " ").replace("\\", " ") + '"'


def tidy(text: str | None) -> str | None:
    """Collapse runs of whitespace; None for missing or blank text."""
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(" ", text).strip()
    return cleaned or None


def strip_markup(text: str | None) -> str | None:
    """Reduce an HTML or JATS fragment to its words, for screening and display."""
    if text is None:
        return None
    return tidy(html.unescape(_MARKUP.sub(" ", text)))


def bare_doi(doi: object) -> str | None:
    """A DOI without its resolver prefix, case kept as the source gave it.

    Identity comparison case-folds on its own (`PaperRecord.identity`); the stored
    value stays as published.
    """
    if not isinstance(doi, str) or not doi.strip():
        return None
    return _DOI_RESOLVER.sub("", doi.strip())
