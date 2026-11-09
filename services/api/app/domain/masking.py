"""Masking as a pure function of raw text and spans (architecture §3.9.1).

What belongs here: the entity types and the lists of them each kind of text is
masked with, a `MaskSpan` and its provenance, project `MaskingRule`s, applying
spans to text, and the checks the prompt guard shares with the sanitiser.

What does not: finding spans (detectors live in `app/privacy/`), storing them,
or encrypting anything. Raw text is never edited; masked text is rebuilt from
the raw text and its spans whenever the spans change.

Masking is not anonymity. Detection misses formats and rare details still
identify people; everything here is built to be checked and corrected, not
trusted.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

MAX_MASKING_TERM_LENGTH = 200
MIN_MASKING_TERM_LENGTH = 2
MAX_MASKING_RULES_PER_PROJECT = 500


class EntityType(StrEnum):
    """What a span hides. The value is the placeholder's prefix: `[PERSON_1]`."""

    PERSON = "PERSON"
    LOCATION = "LOCATION"
    DATE = "DATE"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    MEDICAL_RECORD = "MEDICAL_RECORD"
    ID_NUMBER = "ID_NUMBER"
    PASSPORT = "PASSPORT"
    IBAN = "IBAN"
    CARD_NUMBER = "CARD_NUMBER"
    IP_ADDRESS = "IP_ADDRESS"
    COORDINATES = "COORDINATES"


STRUCTURED_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.EMAIL,
        EntityType.PHONE,
        EntityType.MEDICAL_RECORD,
        EntityType.ID_NUMBER,
        EntityType.PASSPORT,
        EntityType.IBAN,
        EntityType.CARD_NUMBER,
        EntityType.IP_ADDRESS,
        EntityType.COORDINATES,
    }
)

# Published papers name places, populations and authors as part of the
# evidence: masking "Marib" in an abstract would break screening against a
# setting criterion. Papers get contacts and identifiers only.
PAPER_ENTITY_TYPES: frozenset[EntityType] = STRUCTURED_ENTITY_TYPES

# Years and date ranges in a question are search scope ("2019 to 2023"), so a
# question keeps its dates.
QUESTION_ENTITY_TYPES: frozenset[EntityType] = STRUCTURED_ENTITY_TYPES | {
    EntityType.PERSON,
    EntityType.LOCATION,
}

# Brief rule 3: names, emails, phone numbers, dates, places, medical record and
# insurance ids. It cannot be switched off for uploaded documents.
DOCUMENT_ENTITY_TYPES: frozenset[EntityType] = QUESTION_ENTITY_TYPES | {EntityType.DATE}

# When spans of different types overlap, the merged region takes the most
# specific type: an email is still an email even if a name model saw a name in it.
_PRECEDENCE: tuple[EntityType, ...] = (
    EntityType.EMAIL,
    EntityType.MEDICAL_RECORD,
    EntityType.ID_NUMBER,
    EntityType.PASSPORT,
    EntityType.IBAN,
    EntityType.CARD_NUMBER,
    EntityType.PHONE,
    EntityType.IP_ADDRESS,
    EntityType.COORDINATES,
    EntityType.PERSON,
    EntityType.LOCATION,
    EntityType.DATE,
)

PLACEHOLDER = re.compile(r"\[(?:" + "|".join(t.value for t in EntityType) + r")_[0-9]+\]")

# A space before the number is not accepted: "date 2020" is a search term, and
# a placeholder rewritten as "Location 1" is left to the planner's instructions.
_PLACEHOLDER_ANY_FORM = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(t.value.replace("_", "[ _-]") for t in EntityType)
    + r")[_-][0-9]+(?![0-9])",
    re.IGNORECASE,
)


def holds_placeholder(text: str) -> bool:
    """Whether `text` holds a placeholder, even one a model re-bracketed or lower-cased.

    `PLACEHOLDER` matches only what masking writes, `[LOCATION_1]`. A model
    copying one into its answer may write `<LOCATION_1>` or `location_1 camps`.
    """
    return _PLACEHOLDER_ANY_FORM.search(text) is not None


_DETECTOR_SOURCE = re.compile(r"[a-z0-9][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.+-]*")
_ID_SOURCE = re.compile(
    r"(?:user|rule):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

# Characters that reorder or hide the text around them, as for project names.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


def detector_source(name: str, version: str) -> str:
    """The provenance of a detector's span: `"patterns@1"`, `"spacy-en_core_web_sm@3.8.0"`."""
    return f"{name}@{version}"


def user_source(user_id: UUID) -> str:
    """The provenance of a span a person marked by hand."""
    return f"user:{user_id}"


def rule_source(rule_id: UUID) -> str:
    """The provenance of a span a project masking rule produced."""
    return f"rule:{rule_id}"


@dataclass(frozen=True, slots=True, order=True)
class MaskSpan:
    """A masked range `[start, end)` of a raw text, its type, and what found it.

    `source` is a detector and its version, `user:<id>` or `rule:<id>`, so
    "why was this masked, and by what?" always has an answer.
    """

    start: int
    end: int
    entity_type: EntityType
    source: str

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("MaskSpan.start must not be negative")
        if self.end <= self.start:
            raise ValueError("MaskSpan.end must be after MaskSpan.start")
        if not (_DETECTOR_SOURCE.fullmatch(self.source) or _ID_SOURCE.fullmatch(self.source)):
            raise ValueError(
                "MaskSpan.source must be 'detector@version', 'user:<id>' or 'rule:<id>'"
            )

    def to_json(self) -> dict[str, str | int]:
        return {
            "start": self.start,
            "end": self.end,
            "entity_type": self.entity_type.value,
            "source": self.source,
        }


def spans_to_json(spans: Sequence[MaskSpan]) -> list[dict[str, str | int]]:
    """Spans as JSON-ready dicts, for sealing under the project's key."""
    return [s.to_json() for s in spans]


def spans_from_json(items: Sequence[Mapping[str, object]]) -> tuple[MaskSpan, ...]:
    """The inverse of `spans_to_json`. Raises `ValueError` for anything malformed."""
    spans: list[MaskSpan] = []
    for item in items:
        start, end = item.get("start"), item.get("end")
        kind, source = item.get("entity_type"), item.get("source")
        if not (isinstance(start, int) and isinstance(end, int)):
            raise ValueError("MaskSpan offsets must be integers")
        if not (isinstance(kind, str) and isinstance(source, str)):
            raise ValueError("MaskSpan entity_type and source must be strings")
        spans.append(MaskSpan(start, end, EntityType(kind), source))
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class MaskedText:
    """Text with its spans applied.

    `spans` point into the raw text, not into `masked`. `counts` is the number
    of distinct masked values per entity type: what a privacy receipt shows,
    never the values themselves.
    """

    masked: str
    spans: tuple[MaskSpan, ...]
    counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if any(n < 1 for n in self.counts.values()):
            raise ValueError("MaskedText.counts must be positive")
        object.__setattr__(self, "counts", MappingProxyType(dict(sorted(self.counts.items()))))

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _region_type(group: Sequence[MaskSpan]) -> EntityType:
    return min(
        group, key=lambda s: (_PRECEDENCE.index(s.entity_type), -(s.end - s.start))
    ).entity_type


def _merged_regions(spans: Sequence[MaskSpan]) -> list[tuple[int, int, EntityType]]:
    regions: list[tuple[int, int, EntityType]] = []
    group: list[MaskSpan] = []
    end = -1
    for span in spans:
        if group and span.start >= end:
            regions.append((group[0].start, end, _region_type(group)))
            group = []
        end = max(end, span.end) if group else span.end
        group.append(span)
    if group:
        regions.append((group[0].start, end, _region_type(group)))
    return regions


def _value_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def apply_spans(raw: str, spans: Sequence[MaskSpan]) -> MaskedText:
    """Replace every span of `raw` with a placeholder such as `[PERSON_1]`.

    Overlapping spans merge into one region: over-masking wins. Within one
    text, the same value (ignoring case and spacing) always gets the same
    placeholder, and different values of a type are numbered in order of first
    appearance. The result does not depend on the order of `spans`.
    """
    ordered = tuple(sorted(set(spans)))
    if ordered and max(s.end for s in ordered) > len(raw):
        raise ValueError("MaskSpan.end must lie within the text")
    numbers: dict[tuple[EntityType, str], int] = {}
    per_type: Counter[EntityType] = Counter()
    pieces: list[str] = []
    cursor = 0
    for start, end, kind in _merged_regions(ordered):
        key = (kind, _value_key(raw[start:end]))
        if key not in numbers:
            per_type[kind] += 1
            numbers[key] = per_type[kind]
        pieces.append(raw[cursor:start])
        pieces.append(f"[{kind.value}_{numbers[key]}]")
        cursor = end
    pieces.append(raw[cursor:])
    counts = {kind.value: n for kind, n in per_type.items()}
    return MaskedText("".join(pieces), ordered, counts)


def unmasked(text: str, spans: Sequence[MaskSpan]) -> tuple[MaskSpan, ...]:
    """The spans that are not entirely inside a placeholder: what would actually leak."""
    placeholders = [(m.start(), m.end()) for m in PLACEHOLDER.finditer(text)]
    return tuple(
        s
        for s in spans
        if not any(start <= s.start and s.end <= end for start, end in placeholders)
    )


def leak_reason(found: Mapping[EntityType, int]) -> str:
    """Why a prompt was refused, in counts and types only, never values."""
    listed = ", ".join(f"{n} {kind.value}" for kind, n in sorted(found.items()))
    return f"The prompt still contains unmasked personal data ({listed}); nothing was sent."


def normalize_masking_term(term: str) -> str:
    """Trim the term and collapse inner whitespace; refuse one that cannot work as a rule."""
    normalised = " ".join(term.split())
    if len(normalised) < MIN_MASKING_TERM_LENGTH:
        raise ValueError(
            f"MaskingRule.term must be at least {MIN_MASKING_TERM_LENGTH} characters"
        )
    if len(normalised) > MAX_MASKING_TERM_LENGTH:
        raise ValueError(
            f"MaskingRule.term must be at most {MAX_MASKING_TERM_LENGTH} characters"
        )
    if any(
        ch in _BIDI_CONTROLS or unicodedata.category(ch) in _FORBIDDEN_CATEGORIES
        for ch in normalised
    ):
        raise ValueError("MaskingRule.term must not contain control characters")
    if not any(ch.isalnum() for ch in normalised):
        raise ValueError("MaskingRule.term must contain a letter or a digit")
    return normalised


@dataclass(frozen=True, slots=True)
class MaskingRule:
    """A project-wide term that is always masked, as `entity_type`, in every text."""

    id: UUID
    term: str
    entity_type: EntityType

    def __post_init__(self) -> None:
        if normalize_masking_term(self.term) != self.term:
            raise ValueError("MaskingRule.term must already be normalised")


@dataclass(frozen=True, slots=True)
class MaskingRuleRecord:
    """A masking rule as stored: the term only sealed under the project's key."""

    id: UUID
    project_id: UUID
    term_sealed: bytes
    entity_type: EntityType
    created_by: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("MaskingRuleRecord.created_at must be timezone-aware")
        if not self.term_sealed:
            raise ValueError("MaskingRuleRecord.term_sealed must not be empty")


class DuplicateMaskingRuleError(Exception):
    """The project already masks this term. The message is safe to show."""


def _rule_pattern(term: str) -> re.Pattern[str]:
    words = (re.escape(word) for word in term.split())
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def rule_spans(text: str, rules: Sequence[MaskingRule]) -> tuple[MaskSpan, ...]:
    """A span over every whole-word, case-insensitive occurrence of each rule's term."""
    return tuple(
        MaskSpan(match.start(), match.end(), rule.entity_type, rule_source(rule.id))
        for rule in rules
        for match in _rule_pattern(rule.term).finditer(text)
    )


def masking_fingerprint(
    detector_sources: Collection[str], rules: Sequence[MaskingRule]
) -> tuple[str, ...]:
    """What a text was masked with: detectors and their versions, and the project's rules.

    A stored masking with the same fingerprint is current; any other must be
    redone, because a detector or a rule changed since.
    """
    return (*sorted(detector_sources), *sorted(rule_source(r.id) for r in rules))


@dataclass(frozen=True, slots=True)
class PaperMask:
    """A paper's masked text and spans as stored: both sealed under the project's key.

    The raw text stays where ingest kept it (`paper_texts`); this record holds
    only what is derived from it, and `masked_with` says from what, so a re-run
    can tell a current masking from one a detector or rule has since outdated.
    """

    paper_id: UUID
    project_id: UUID
    masked_sealed: bytes
    spans_sealed: bytes
    masked_with: tuple[str, ...]
    masked_at: datetime

    def __post_init__(self) -> None:
        if self.masked_at.tzinfo is None:
            raise ValueError("PaperMask.masked_at must be timezone-aware")
        if not self.masked_sealed or not self.spans_sealed:
            raise ValueError("PaperMask sealed fields must not be empty")
        if not self.masked_with:
            raise ValueError("PaperMask.masked_with must name at least one detector")


@dataclass(frozen=True, slots=True)
class DocumentAwaitingReview:
    """An uploaded document whose masking no person has approved yet. Counts only."""

    document_id: UUID
    counts: Mapping[EntityType, int]
