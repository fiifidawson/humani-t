"""Requirements for matching text in a search across projects (UI_UX_SPEC §7.6, ADR 0018).

The query is a few words; a text matches when it holds every word, ignoring case and
accents. Results are capped per scope in each project and in all, and say when they were.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.domain.project_search import (
    EXCERPT_LENGTH,
    MAX_HITS,
    MAX_HITS_PER_SCOPE,
    MAX_QUERY_LENGTH,
    ProjectHits,
    SearchHit,
    SearchQuery,
    SearchScope,
    assemble,
    per_scope,
)


def hit(scope: SearchScope = SearchScope.BRIEFS, text: str = "Prices rose") -> SearchHit:
    return SearchHit(scope=scope, text=text)


def group(n: int, hits: tuple[SearchHit, ...]) -> ProjectHits:
    return ProjectHits(UUID(int=n), f"Project {n}", is_archived=False, hits=hits)


class TestTheQuery:
    def test_a_query_keeps_its_words_as_typed_but_trimmed(self):
        query = SearchQuery("  price   rises ")

        assert query.text == "price rises"

    @pytest.mark.parametrize("text", ["", "   ", "a", " é "])
    def test_a_query_shorter_than_two_characters_is_refused(self, text):
        with pytest.raises(ValueError, match=r"SearchQuery\.text"):
            SearchQuery(text)

    def test_a_query_longer_than_the_limit_is_refused(self):
        with pytest.raises(ValueError, match=r"SearchQuery\.text"):
            SearchQuery("a" * (MAX_QUERY_LENGTH + 1))

    def test_a_query_with_control_characters_is_refused(self):
        with pytest.raises(ValueError, match=r"SearchQuery\.text"):
            SearchQuery("cash\x00vouchers")

    def test_no_scope_means_every_scope(self):
        query = SearchQuery("cash")

        assert all(query.covers(scope) for scope in SearchScope)

    def test_a_scope_narrows_the_search_to_that_scope(self):
        query = SearchQuery("cash", SearchScope.PAPERS)

        assert query.covers(SearchScope.PAPERS)
        assert not query.covers(SearchScope.BRIEFS)


class TestMatching:
    def test_a_text_matches_when_it_holds_every_word_in_any_order(self):
        query = SearchQuery("rises price")

        assert query.excerpt("How fast do prices rise? Prices rises in Gao.") is not None

    def test_a_text_missing_one_word_does_not_match(self):
        query = SearchQuery("cash vouchers")

        assert query.excerpt("Cash transfers in camps") is None

    def test_case_and_accents_are_ignored(self):
        query = SearchQuery("securite ALIMENTAIRE")

        assert query.excerpt("La sécurité alimentaire à Mopti") is not None

    def test_a_word_matches_inside_a_longer_word(self):
        query = SearchQuery("price")

        assert query.excerpt("Food prices rose") is not None


class TestTheExcerpt:
    def test_a_short_text_is_the_whole_excerpt_on_one_line(self):
        query = SearchQuery("cash")

        assert query.excerpt("Cash transfers\n\nin camps") == "Cash transfers in camps"

    def test_a_long_text_is_cut_around_the_first_match_and_marked_as_cut(self):
        text = "Background. " * 40 + "Cash transfers raised prices. " + "More. " * 40
        excerpt = SearchQuery("cash").excerpt(text)

        assert excerpt is not None
        assert "Cash transfers raised prices." in excerpt
        assert excerpt.startswith("…")
        assert excerpt.endswith("…")
        assert len(excerpt) <= EXCERPT_LENGTH + 2

    def test_the_excerpt_finds_the_match_after_text_that_folds_to_more_characters(self):
        text = "ß" * 300 + " cash"

        excerpt = SearchQuery("cash").excerpt(text)

        assert excerpt is not None
        assert excerpt.endswith("cash")


class TestCaps:
    def test_each_scope_keeps_at_most_its_cap_in_order(self):
        briefs = tuple(hit(text=f"claim {n}") for n in range(MAX_HITS_PER_SCOPE + 2))
        papers = (hit(SearchScope.PAPERS),)

        kept, is_truncated = per_scope((*briefs, *papers))

        assert kept == (*briefs[:MAX_HITS_PER_SCOPE], *papers)
        assert is_truncated

    def test_hits_under_every_cap_are_all_kept(self):
        hits = (hit(), hit(SearchScope.PAPERS))

        assert per_scope(hits) == (hits, False)

    def test_projects_without_hits_are_left_out(self):
        results = assemble([group(1, ()), group(2, (hit(),))], is_partial=False)

        assert [g.project_id for g in results.groups] == [UUID(int=2)]
        assert not results.is_truncated

    def test_the_results_stop_at_the_overall_cap_and_say_so(self):
        groups = [group(n, (hit(),) * MAX_HITS_PER_SCOPE) for n in range(MAX_HITS)]

        results = assemble(groups, is_partial=False)

        assert sum(len(g.hits) for g in results.groups) == MAX_HITS
        assert results.is_truncated

    def test_results_from_a_partial_search_say_they_are_truncated(self):
        results = assemble([group(1, (hit(),))], is_partial=True)

        assert results.is_truncated
