"""Okapi BM25 over a review's passages, in process: the P1 retriever (ADR-0007).

A review's corpus is a few hundred masked abstracts, so the index is built per
search in milliseconds and nothing is kept between searches: no new service,
no stored vectors to keep in step with masking, and the same inputs always
give the same ranking.

Known and accepted: matching is lexical. "cash" does not find "money", and no
stemming means "price" does not find "prices". The planner and the critic ask
several phrasings, which recovers some of that.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from app.domain.screening import Passage

# Masked placeholders such as [PERSON_1] stay whole words: "person_1".
_TOKEN = re.compile(r"[a-z0-9_]+")

# The usual Okapi defaults (Robertson and Zaragoza, 2009).
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


class Bm25Retriever:
    @property
    def retriever_id(self) -> str:
        return "bm25-okapi/1"

    async def search(
        self, corpus: Sequence[Passage], query: str, *, limit: int
    ) -> tuple[Passage, ...]:
        terms = set(tokenize(query))
        if not corpus or not terms or limit < 1:
            return ()
        documents = [Counter(tokenize(p.text)) for p in corpus]
        average_length = sum(d.total() for d in documents) / len(documents) or 1.0
        weights = {t: _idf(t, documents) for t in terms}
        scored = [
            (_score(d, weights, average_length), position)
            for position, d in enumerate(documents)
        ]
        # Ties keep corpus order, so a search is reproducible.
        ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        return tuple(corpus[position] for _, position in ranked[:limit])


def _idf(term: str, documents: Sequence[Counter[str]]) -> float:
    containing = sum(1 for d in documents if term in d)
    # The "+ 1" keeps the weight positive for a term in most passages.
    return math.log((len(documents) - containing + 0.5) / (containing + 0.5) + 1)


def _score(document: Counter[str], weights: dict[str, float], average_length: float) -> float:
    length_norm = _K1 * (1 - _B + _B * document.total() / average_length)
    score = 0.0
    for term, weight in weights.items():
        frequency = document[term]
        if frequency:
            score += weight * frequency * (_K1 + 1) / (frequency + length_norm)
    return score
