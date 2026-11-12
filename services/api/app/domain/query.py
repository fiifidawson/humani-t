"""Source-agnostic Boolean queries.

A `BooleanQuery` is a structure, not a string: each provider compiles it into its own
dialect, so one search is reproducible across sources that agree on nothing. A concept's
terms are OR'd, the concepts in a theme are OR'd, and themes are AND'd.
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

    The vocabulary's version and origin travel with the query, so a result set can say
    which terms produced it without a database round trip.
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

        Never send it to a repository: providers compile their own dialect.
        """
        clauses: list[str] = []
        for theme in self.themes:
            terms = " OR ".join(f'"{t}"' for t in theme.terms)
            clauses.append(f"({terms})")
        return " AND ".join(clauses)


def _group_for(concept: Concept) -> TermGroup:
    return TermGroup(concept_term=concept.term, terms=concept.terms)


def compile_vocabulary(vocabulary: Vocabulary) -> BooleanQuery:
    """Turn a Vocabulary into a BooleanQuery, the same way every time.

    No model is consulted here, for either origin. That is what makes a user-supplied
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
