"""Opt-in smoke test against the real paper sources. Skipped unless asked for.

Run it with `HUMANI_T_LIVE_SOURCES=1`. It sends one small, non-sensitive query
to each source through the real SSRF-guarded fetcher, so it confirms what the
recorded fixtures cannot: that the APIs still answer in the shape we parse.
Without an OpenAlex key it spends a little of OpenAlex's shared free budget.
"""

from __future__ import annotations

import os

import pytest

from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.net.base import Destination, OutboundPolicy
from app.net.httpx_fetcher import HttpxFetcher
from app.search.arxiv import ArxivProvider
from app.search.base import PaperSearchProvider
from app.search.europepmc import EuropePmcProvider
from app.search.openalex import OpenAlexProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("HUMANI_T_LIVE_SOURCES") != "1",
    reason="live network test; set HUMANI_T_LIVE_SOURCES=1 to run",
)

QUERY = compile_vocabulary(
    Vocabulary(
        concepts=(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", theme="setting"),
        ),
        origin=VocabularyOrigin.USER_SUPPLIED,
    )
)


@pytest.mark.parametrize(
    ("host", "build"),
    [
        ("api.openalex.org", OpenAlexProvider),
        ("www.ebi.ac.uk", EuropePmcProvider),
        ("export.arxiv.org", ArxivProvider),
    ],
)
async def test_the_live_source_answers_in_the_shape_we_parse(host, build):
    fetcher = HttpxFetcher(OutboundPolicy(destinations=(Destination(host),)))
    provider: PaperSearchProvider = build(fetcher)
    try:
        found = await provider.search(QUERY, limit=3)
    finally:
        await fetcher.close()

    assert found, f"{provider.source_id} returned nothing for a query that has hits"
    assert all(r.source_id == provider.source_id and r.title for r in found)
