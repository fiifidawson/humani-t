"""Source-agnostic Boolean queries.

A BooleanQuery is NOT a string. Each repository (arXiv, OpenAlex, Europe PMC,
Google Scholar) has its own syntax, escaping rules and field names; a provider
compiles this structure into its own dialect. Keeping the structure means one
search is reproducible across four sources that agree on nothing.

Shape, following systematic-review practice:

    each Concept        ->  (term OR synonym OR synonym)
    concepts in a theme ->  OR'd together
    themes              ->  AND'd together

    ("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis")
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin


@dataclass(frozen=True, slots=True)
class TermGroup:
    """The terms of a single Concept, OR'd together."""

    concept_term: str
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("TermGroup needs at least one term")


@dataclass(frozen=True, slots=True)
class ThemeGroup:
    """Every Concept in one theme. Their groups are OR'd together."""

    theme: str
    groups: tuple[TermGroup, ...]

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("ThemeGroup needs at least one TermGroup")

    @property
    def terms(self) -> tuple[str, ...]:
        """Every term in the theme, flattened."""
        return tuple(term for group in self.groups for term in group.terms)


@dataclass(frozen=True, slots=True)
class BooleanQuery:
    """Themes AND'd together, plus the provenance of the terms.

    `vocabulary_version` and `vocabulary_origin` travel with the query rather
    than being looked up later. A result set must be able to answer 'which terms
    produced me' without a database round trip, because it may be read on a
    different machine.
    """

    themes: tuple[ThemeGroup, ...]
    vocabulary_version: int
    vocabulary_origin: VocabularyOrigin

    def __post_init__(self) -> None:
        if not self.themes:
            raise ValueError("BooleanQuery needs at least one ThemeGroup")

    @property
    def is_strict(self) -> bool:
        return self.vocabulary_origin is VocabularyOrigin.USER_SUPPLIED

    @property
    def all_terms(self) -> tuple[str, ...]:
        return tuple(term for theme in self.themes for term in theme.terms)

    def render(self) -> str:
        """Human-readable form, for logs, tests and the audit trail.

        NOT for sending to a repository. Providers compile their own dialect —
        if you find yourself passing this to an HTTP client, a seam has leaked.
        """
        clauses = []
        for theme in self.themes:
            terms = " OR ".join(f'"{t}"' for t in theme.terms)
            clauses.append(f"({terms})")
        return " AND ".join(clauses)


def _group_for(concept: Concept) -> TermGroup:
    return TermGroup(concept_term=concept.term, terms=concept.terms)


def compile_vocabulary(vocabulary: Vocabulary) -> BooleanQuery:
    """Turn a Vocabulary into a BooleanQuery.

    Pure and total: the same Vocabulary always compiles to the same query. No
    model is consulted here, for either origin — expansion, if it happens at
    all, happens before a Vocabulary exists. That is what makes a user-supplied
    vocabulary strict by construction rather than by discipline.
    """
    themes = tuple(
        ThemeGroup(
            theme=theme,
            groups=tuple(_group_for(c) for c in vocabulary.concepts_in(theme)),
        )
        for theme in vocabulary.themes
    )
    return BooleanQuery(
        themes=themes,
        vocabulary_version=vocabulary.version,
        vocabulary_origin=vocabulary.origin,
    )
