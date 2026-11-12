"""The curated term list a review searches with.

A Vocabulary is the most valuable artefact in the system: it is what makes a
search reproducible, and it is the thing a reviewer argues about. Everything
downstream inherits its provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

DEFAULT_THEME = "general"


class VocabularyOrigin(StrEnum):
    """Where the terms came from.

    Recorded and surfaced, never inferred. A reviewer must be able to tell
    whether a term was proposed by a model or written by a person.
    """

    LLM_ASSISTED = "llm_assisted"
    USER_SUPPLIED = "user_supplied"


def _dedupe_preserving_order(values: tuple[str, ...]) -> tuple[str, ...]:
    """Drop blanks and case-insensitive duplicates, keeping first spelling."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            kept.append(cleaned)
    return tuple(kept)


@dataclass(frozen=True, slots=True)
class Concept:
    """One research concept and the words that stand in for it.

    `term` is the canonical spelling. `synonyms` are alternatives that should
    match the same idea. Together they become one OR-group in a query.
    """

    term: str
    synonyms: tuple[str, ...] = ()
    theme: str = DEFAULT_THEME

    def __post_init__(self) -> None:
        if not self.term.strip():
            raise ValueError("Concept.term must not be blank")
        if not self.theme.strip():
            raise ValueError("Concept.theme must not be blank")

    @property
    def terms(self) -> tuple[str, ...]:
        """Canonical term first, then synonyms, de-duplicated.

        Order matters: it is what a compiled query will show, and a reviewer
        reading the query should see the term they chose at the front.
        """
        return _dedupe_preserving_order((self.term, *self.synonyms))


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """A versioned, immutable set of Concepts.

    Immutable on purpose. Editing produces a new version rather than mutating
    this one, because search results reference the version they came from. Allow
    in-place edits and reproducibility is gone — silently.
    """

    concepts: tuple[Concept, ...]
    origin: VocabularyOrigin
    version: int = 1
    frozen_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.concepts:
            raise ValueError("Vocabulary must contain at least one Concept")
        if self.version < 1:
            raise ValueError("Vocabulary.version starts at 1")

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None

    @property
    def is_strict(self) -> bool:
        """True when the user supplied these terms themselves.

        They must then reach the search providers exactly as given: a correctness property,
        not a preference.
        """
        return self.origin is VocabularyOrigin.USER_SUPPLIED

    @property
    def themes(self) -> tuple[str, ...]:
        """Distinct themes, in first-appearance order."""
        return _dedupe_preserving_order(tuple(c.theme for c in self.concepts))

    def concepts_in(self, theme: str) -> tuple[Concept, ...]:
        key = theme.strip().casefold()
        return tuple(c for c in self.concepts if c.theme.strip().casefold() == key)

    def freeze(self, *, at: datetime | None = None) -> Vocabulary:
        """Return a frozen copy, marking the moment the user confirmed it."""
        if self.is_frozen:
            return self
        return replace(self, frozen_at=at or datetime.now(UTC))

    def revise(self, concepts: tuple[Concept, ...]) -> Vocabulary:
        """Return the next version. The current one is left untouched."""
        return Vocabulary(
            concepts=concepts,
            origin=self.origin,
            version=self.version + 1,
            frozen_at=None,
        )
