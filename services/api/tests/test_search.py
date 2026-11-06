"""The PaperSearchProvider seam, exercised through the fake."""

from __future__ import annotations

import pytest

from app.domain.paper import PaperRecord
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider


def query_for(*concepts: Concept, origin=VocabularyOrigin.LLM_ASSISTED):
    return compile_vocabulary(Vocabulary(concepts=concepts, origin=origin))


@pytest.fixture
def provider() -> FakePaperSearchProvider:
    return FakePaperSearchProvider()


class TestConformance:
    def test_fake_satisfies_the_protocol_without_inheriting_it(self, provider):
        """Structural typing: no base class, no registration, still conforms."""
        assert isinstance(provider, PaperSearchProvider)
        assert FakePaperSearchProvider.__mro__[1:] == (object,)

    def test_source_id_is_stable(self, provider):
        assert provider.source_id == "fake"


class TestSearch:
    async def test_and_across_themes_excludes_single_theme_matches(self, provider):
        """The discriminating case: 'chatbot' alone must not be enough."""
        query = query_for(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", ("crisis",), theme="setting"),
        )

        found = await provider.search(query)
        ids = {r.external_id for r in found}

        assert "fake-001" in ids  # chatbot + humanitarian
        assert "fake-002" in ids  # chatbot + humanitarian
        assert "fake-003" not in ids  # chatbot only
        assert "fake-004" not in ids  # humanitarian only

    async def test_or_within_a_theme_widens_the_result(self, provider):
        narrow = await provider.search(query_for(Concept("logistics")))
        wide = await provider.search(query_for(Concept("logistics", ("triage",))))

        assert len(wide) > len(narrow)

    async def test_synonym_alone_is_enough_to_match(self, provider):
        query = query_for(Concept("nonexistent term", ("crisis response",)))
        found = await provider.search(query)

        assert {r.external_id for r in found} == {"fake-001"}

    async def test_no_match_returns_empty_not_error(self, provider):
        assert await provider.search(query_for(Concept("quantum chromodynamics"))) == ()

    async def test_limit_is_respected(self, provider):
        query = query_for(Concept("chatbot", ("humanitarian",)))
        assert len(await provider.search(query, limit=1)) == 1

    async def test_every_record_is_stamped_with_the_source(self, provider):
        found = await provider.search(query_for(Concept("chatbot")))

        assert found
        assert all(r.source_id == provider.source_id for r in found)

    async def test_provider_receives_the_compiled_query_unmodified(self, provider):
        query = query_for(Concept("chatbot"), origin=VocabularyOrigin.USER_SUPPLIED)

        await provider.search(query)

        assert provider.calls == [query]
        assert provider.calls[0].is_strict


class TestFulltext:
    async def test_open_access_paper_returns_text(self, provider):
        found = await provider.search(query_for(Concept("crisis response")))
        assert await provider.fetch_fulltext(found[0]) is not None

    async def test_closed_paper_returns_none_rather_than_raising(self, provider):
        closed = PaperRecord("fake", "fake-003", "A survey of chatbot architectures")
        assert await provider.fetch_fulltext(closed) is None


class TestSubstitution:
    async def test_a_custom_corpus_swaps_in_without_touching_callers(self):
        """The seam working: the caller does not know which corpus it queried."""
        custom = FakePaperSearchProvider(
            corpus=(PaperRecord("fake", "x-1", "Ethics of triage algorithms", 2025),)
        )

        found = await custom.search(query_for(Concept("triage")))

        assert [r.external_id for r in found] == ["x-1"]
