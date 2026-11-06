"""Lexical retrieval over a review's included passages (ADR-0007).

Okapi BM25, in process. These specs drive the real retriever: it makes no
network call and holds nothing between searches, so a fake would prove less.
"""

from __future__ import annotations

import uuid

from app.briefs.base import Retriever
from app.briefs.bm25 import Bm25Retriever
from app.domain.briefs import SourceKind
from app.domain.screening import Passage


def passage(tag: str, text: str) -> Passage:
    return Passage(tag, uuid.uuid4(), SourceKind.PAPER, text, "abstract")


CORPUS = (
    passage("P0001", "Cash transfers raised staple food prices in small markets."),
    passage("P0002", "In-kind food baskets reduced acute malnutrition among children."),
    passage("P0003", "Vouchers limited price risk but cost more to run than cash."),
    passage("P0004", "Protection risks for women receiving cash in [LOCATION_1]."),
)


class TestBm25Retriever:
    async def test_it_is_a_retriever(self):
        retriever: Retriever = Bm25Retriever()

        assert await retriever.search(CORPUS, "food prices", limit=1)

    async def test_the_passage_sharing_the_most_query_terms_ranks_first(self):
        found = await Bm25Retriever().search(CORPUS, "cash food prices markets", limit=4)

        assert found[0].tag == "P0001"

    async def test_a_rare_term_outweighs_a_common_one(self):
        found = await Bm25Retriever().search(CORPUS, "malnutrition cash", limit=1)

        assert [p.tag for p in found] == ["P0002"]

    async def test_passages_sharing_no_term_with_the_query_are_not_returned(self):
        found = await Bm25Retriever().search(CORPUS, "earthquake shelter", limit=4)

        assert found == ()

    async def test_at_most_limit_passages_are_returned(self):
        found = await Bm25Retriever().search(CORPUS, "cash", limit=2)

        assert len(found) == 2

    async def test_matching_ignores_case_and_punctuation(self):
        found = await Bm25Retriever().search(CORPUS, "VOUCHERS, Price-Risk!", limit=1)

        assert [p.tag for p in found] == ["P0003"]

    async def test_masked_placeholders_are_matched_as_ordinary_terms(self):
        found = await Bm25Retriever().search(CORPUS, "[LOCATION_1]", limit=1)

        assert [p.tag for p in found] == ["P0004"]

    async def test_ties_keep_the_corpus_order_so_a_search_is_reproducible(self):
        twins = (passage("P0010", "cash"), passage("P0011", "cash"))

        found = await Bm25Retriever().search(twins, "cash", limit=2)

        assert [p.tag for p in found] == ["P0010", "P0011"]

    async def test_an_empty_corpus_finds_nothing(self):
        assert await Bm25Retriever().search((), "cash", limit=3) == ()
