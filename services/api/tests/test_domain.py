"""Domain rules: vocabulary immutability, query compilation, paper identity."""

from __future__ import annotations

import pytest

from app.domain.paper import PaperRecord, deduplicate, normalise_doi, normalise_title
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin


def make_vocabulary(origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED) -> Vocabulary:
    return Vocabulary(
        concepts=(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", ("crisis response",), theme="setting"),
        ),
        origin=origin,
    )


class TestConcept:
    def test_terms_put_canonical_first(self):
        concept = Concept("chatbot", ("conversational agent", "dialogue system"))
        assert concept.terms == ("chatbot", "conversational agent", "dialogue system")

    def test_terms_drop_case_insensitive_duplicates(self):
        concept = Concept("Chatbot", ("chatbot", "CHATBOT", "conversational agent"))
        assert concept.terms == ("Chatbot", "conversational agent")

    def test_terms_drop_blanks(self):
        assert Concept("chatbot", ("", "   ")).terms == ("chatbot",)

    def test_blank_term_is_rejected(self):
        with pytest.raises(ValueError, match="term must not be blank"):
            Concept("   ")


class TestVocabulary:
    def test_empty_vocabulary_is_rejected(self):
        with pytest.raises(ValueError, match="at least one Concept"):
            Vocabulary(concepts=(), origin=VocabularyOrigin.USER_SUPPLIED)

    def test_themes_are_in_first_appearance_order(self):
        assert make_vocabulary().themes == ("technology", "setting")

    def test_revise_leaves_the_original_untouched(self):
        original = make_vocabulary()
        revised = original.revise((Concept("triage"),))

        assert original.version == 1
        assert revised.version == 2
        assert len(original.concepts) == 2
        assert len(revised.concepts) == 1

    def test_freeze_records_the_moment_of_confirmation(self):
        frozen = make_vocabulary().freeze()
        assert frozen.is_frozen
        assert frozen.frozen_at is not None

    def test_freeze_is_idempotent(self):
        once = make_vocabulary().freeze()
        assert once.freeze() is once

    def test_revising_unfreezes_the_new_version(self):
        revised = make_vocabulary().freeze().revise((Concept("triage"),))
        assert not revised.is_frozen

    def test_user_supplied_vocabulary_is_strict(self):
        assert make_vocabulary(VocabularyOrigin.USER_SUPPLIED).is_strict
        assert not make_vocabulary(VocabularyOrigin.LLM_ASSISTED).is_strict


class TestCompilation:
    def test_renders_or_within_theme_and_and_across_themes(self):
        query = compile_vocabulary(make_vocabulary())
        assert query.render() == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")'
        )

    def test_provenance_travels_with_the_query(self):
        vocabulary = make_vocabulary(VocabularyOrigin.USER_SUPPLIED).revise(
            (Concept("chatbot"),)
        )
        query = compile_vocabulary(vocabulary)

        assert query.vocabulary_version == 2
        assert query.vocabulary_origin is VocabularyOrigin.USER_SUPPLIED
        assert query.is_strict

    def test_concepts_sharing_a_theme_are_or_ed_together(self):
        vocabulary = Vocabulary(
            concepts=(
                Concept("chatbot", theme="technology"),
                Concept("llm", ("language model",), theme="technology"),
            ),
            origin=VocabularyOrigin.LLM_ASSISTED,
        )
        query = compile_vocabulary(vocabulary)

        assert len(query.themes) == 1
        assert query.render() == '("chatbot" OR "llm" OR "language model")'

    def test_compilation_is_deterministic(self):
        vocabulary = make_vocabulary()
        assert compile_vocabulary(vocabulary) == compile_vocabulary(vocabulary)

    def test_strict_vocabulary_gains_no_terms(self):
        """The property that makes 'strict' mean strict.

        Compilation is pure: every term in the query came from the vocabulary,
        and nothing was added. If an expansion step is ever introduced upstream,
        this test is what catches it reaching user-supplied terms.
        """
        vocabulary = make_vocabulary(VocabularyOrigin.USER_SUPPLIED)
        supplied = {t.casefold() for c in vocabulary.concepts for t in c.terms}

        compiled = {t.casefold() for t in compile_vocabulary(vocabulary).all_terms}

        assert compiled == supplied


class TestPaperIdentity:
    def test_doi_wins_over_title(self):
        record = PaperRecord("openalex", "W1", "A title", year=2024, doi="10.1/x")
        assert record.identity == "doi:10.1/x"

    def test_doi_resolver_prefix_and_case_are_normalised(self):
        assert normalise_doi("https://doi.org/10.1/ABC") == "10.1/abc"
        assert normalise_doi("doi:10.1/abc") == "10.1/abc"

    def test_same_paper_from_two_sources_shares_identity(self):
        a = PaperRecord("openalex", "W1", "Chatbots In Crisis", 2024, "https://doi.org/10.1/X")
        b = PaperRecord("arxiv", "2401.1", "chatbots in crisis", 2024, "10.1/x")
        assert a.identity == b.identity

    def test_without_doi_identity_falls_back_to_title_and_year(self):
        a = PaperRecord("arxiv", "1", "Chatbots, in crisis!", 2024)
        b = PaperRecord("pmc", "2", "chatbots in crisis", 2024)
        assert a.identity == b.identity

    def test_same_title_different_year_does_not_merge(self):
        """Preprint and published versions stay separate. Known limitation."""
        a = PaperRecord("arxiv", "1", "Chatbots in crisis", 2023)
        b = PaperRecord("pmc", "2", "Chatbots in crisis", 2024)
        assert a.identity != b.identity

    def test_normalise_title_collapses_punctuation_and_case(self):
        assert normalise_title("Chatbots:  In-Crisis!") == "chatbots in crisis"

    def test_blank_title_is_rejected(self):
        with pytest.raises(ValueError, match="title must not be blank"):
            PaperRecord("openalex", "W1", "  ")


class TestDeduplicate:
    def test_first_record_wins_and_order_is_stable(self):
        first = PaperRecord("openalex", "W1", "Shared title", 2024, "10.1/x")
        second = PaperRecord("arxiv", "2401.1", "Shared title", 2024, "10.1/x")
        other = PaperRecord("pmc", "P1", "Different", 2024, "10.1/y")

        merged = deduplicate([first, second, other])

        assert merged == (first, other)
        assert merged[0].source_id == "openalex"

    def test_empty_input_gives_empty_output(self):
        assert deduplicate([]) == ()
