"""A review's search plan: the vocabulary and criteria its runs search and screen with.

An accepted version never changes; editing makes the next one. A user-supplied (strict)
vocabulary is used exactly as given, and no version holding one may be written by the
model. Encryption, storage and model calls belong in `application/planning.py`.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from app.domain.masking import MaskingRule, rule_spans
from app.domain.reviews import InvalidTransitionError
from app.domain.runs import JsonValue
from app.domain.timestamps import require_aware
from app.domain.vocabulary import DEFAULT_THEME, Concept, Vocabulary, VocabularyOrigin

MAX_CRITERION_LENGTH = 500
MAX_CHANGE_REQUEST_LENGTH = 2000
MAX_ANSWER_LENGTH = 1000
MAX_TERM_LENGTH = 200
MAX_TERMS_PER_CONCEPT = 50
MAX_CONCEPTS = 200

_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_TEXT_CONTROLS_ALLOWED = frozenset("\n\t")


class PlanConflictError(InvalidTransitionError):
    """The plan changed, or is not in a state that allows this. Shown to the user as a 409."""


def _has_hidden_characters(text: str, allowed: frozenset[str]) -> bool:
    return any(
        ch in _BIDI_CONTROLS or (unicodedata.category(ch) == "Cc" and ch not in allowed)
        for ch in text
    )


def _clean_text(field_name: str, text: str, limit: int) -> str:
    """Trim free text and refuse one that is blank, too long or hides its own text."""
    trimmed = text.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must not be blank")
    if len(trimmed) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters")
    if _has_hidden_characters(trimmed, _TEXT_CONTROLS_ALLOWED):
        raise ValueError(f"{field_name} must not contain control characters")
    return trimmed


class CriterionKind(StrEnum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


@dataclass(frozen=True, slots=True)
class Criterion:
    """One inclusion or exclusion rule. `id` (I1, E2…) is what a screening decision cites."""

    id: str
    kind: CriterionKind
    text: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Criterion.id must not be blank")
        object.__setattr__(
            self, "text", _clean_text("Criterion.text", self.text, MAX_CRITERION_LENGTH)
        )


def number_criteria(entries: Iterable[tuple[CriterionKind, str]]) -> tuple[Criterion, ...]:
    """Give each criterion its id: I1, I2… for inclusion, E1, E2… for exclusion, in order."""
    counts = {CriterionKind.INCLUSION: 0, CriterionKind.EXCLUSION: 0}
    numbered: list[Criterion] = []
    for kind, text in entries:
        counts[kind] += 1
        prefix = "I" if kind is CriterionKind.INCLUSION else "E"
        numbered.append(Criterion(f"{prefix}{counts[kind]}", kind, text))
    return tuple(numbered)


class PlanMode(StrEnum):
    """How thorough a review is; the interface says Quick and Deep."""

    SHALLOW = "shallow"
    DEEP = "deep"


class AuthorKind(StrEnum):
    MODEL = "model"
    PERSON = "person"


@dataclass(frozen=True, slots=True)
class PlanAuthor:
    """Who wrote a plan version: the model, or a named person. Recorded, never inferred."""

    kind: AuthorKind
    user_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.kind is AuthorKind.PERSON) != (self.user_id is not None):
            raise ValueError("PlanAuthor.user_id is set exactly when a person is the author")

    @classmethod
    def model(cls) -> PlanAuthor:
        return cls(AuthorKind.MODEL)

    @classmethod
    def person(cls, user_id: UUID) -> PlanAuthor:
        return cls(AuthorKind.PERSON, user_id)


@dataclass(frozen=True, slots=True)
class ClarifyingQuestion:
    """A question the model asked before drafting, and the person's answer or skip."""

    id: str
    text: str
    suggestions: tuple[str, ...] = ()
    answer: str | None = None
    is_skipped: bool = False
    answered_by: UUID | None = None
    answered_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("ClarifyingQuestion.id must not be blank")
        if not self.text.strip():
            raise ValueError("ClarifyingQuestion.text must not be blank")
        if self.answer is not None and self.is_skipped:
            raise ValueError("ClarifyingQuestion is answered or skipped, not both")
        require_aware("ClarifyingQuestion", answered_at=self.answered_at)

    @property
    def is_settled(self) -> bool:
        return self.answer is not None or self.is_skipped

    def answered(self, answer: str | None, by: UUID, at: datetime) -> ClarifyingQuestion:
        """Answer, or skip with `None`."""
        if answer is None:
            return replace(self, answer=None, is_skipped=True, answered_by=by, answered_at=at)
        cleaned = _clean_text("ClarifyingQuestion.answer", answer, MAX_ANSWER_LENGTH)
        return replace(self, answer=cleaned, is_skipped=False, answered_by=by, answered_at=at)


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    """What a person asked the model to change, and the version they were looking at."""

    id: UUID
    text: str
    requested_by: UUID
    requested_at: datetime
    based_on_version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "text",
            _clean_text("ChangeRequest.text", self.text, MAX_CHANGE_REQUEST_LENGTH),
        )
        require_aware("ChangeRequest", requested_at=self.requested_at)
        if self.based_on_version < 1:
            raise ValueError("ChangeRequest.based_on_version starts at 1")


@dataclass(frozen=True, slots=True)
class PlanConversation:
    """The planning conversation of one review, between its versions.

    Each version keeps a copy of what informed it, so the conversation can move on without
    losing provenance.
    """

    review_id: UUID
    project_id: UUID
    mode: PlanMode
    created_at: datetime
    created_by: UUID | None
    questions: tuple[ClarifyingQuestion, ...] = ()
    pending_change: ChangeRequest | None = None
    # Optimistic locking: a stale copy can never overwrite a newer one.
    version: int = 0

    def __post_init__(self) -> None:
        require_aware("PlanConversation", created_at=self.created_at)
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("PlanConversation.questions must have distinct ids")

    @property
    def is_ready_to_draft(self) -> bool:
        """Every question answered or skipped. True when there were no questions."""
        return all(q.is_settled for q in self.questions)

    def with_answers(
        self, answers: Mapping[str, str | None], by: UUID, at: datetime
    ) -> PlanConversation:
        """Record answers by question id; `None` skips the question."""
        known = {q.id for q in self.questions}
        unknown = sorted(set(answers) - known)
        if unknown:
            raise ValueError(f"No such clarifying question: {', '.join(unknown)}")
        return replace(
            self,
            questions=tuple(
                q.answered(answers[q.id], by, at) if q.id in answers else q
                for q in self.questions
            ),
        )

    def with_rest_skipped(self, by: UUID, at: datetime) -> PlanConversation:
        """Skip every question not yet answered: "Skip all and draft now"."""
        return replace(
            self,
            questions=tuple(
                q if q.is_settled else q.answered(None, by, at) for q in self.questions
            ),
        )

    def with_pending_change(self, request: ChangeRequest | None) -> PlanConversation:
        return replace(self, pending_change=request)


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    """One version of a review's plan: a vocabulary and criteria, and how they came to be.

    The vocabulary's version is the plan's version, and so is the criteria's: a
    run records one number and can be reproduced from it.
    """

    review_id: UUID
    version: int
    vocabulary: Vocabulary
    criteria: tuple[Criterion, ...]
    author: PlanAuthor
    created_at: datetime
    clarifications: tuple[ClarifyingQuestion, ...] = ()
    change_request: ChangeRequest | None = None
    notes: tuple[str, ...] = ()
    model_id: str | None = None
    prompt_version: str | None = None
    accepted_at: datetime | None = None
    accepted_by: UUID | None = None

    def __post_init__(self) -> None:
        require_aware("ReviewPlan", created_at=self.created_at, accepted_at=self.accepted_at)
        if self.version < 1:
            raise ValueError("ReviewPlan.version starts at 1")
        if self.vocabulary.version != self.version:
            raise ValueError("ReviewPlan.vocabulary.version must equal ReviewPlan.version")
        if (self.accepted_at is None) != (self.accepted_by is None):
            raise ValueError("ReviewPlan.accepted_at and accepted_by are set together")
        if self.vocabulary.frozen_at != self.accepted_at:
            raise ValueError(
                "ReviewPlan.vocabulary is frozen exactly when the plan is accepted"
            )
        is_model = self.author.kind is AuthorKind.MODEL
        if is_model != (self.model_id is not None and self.prompt_version is not None):
            raise ValueError(
                "ReviewPlan.model_id and prompt_version are set exactly when the model wrote it"
            )
        if is_model and self.vocabulary.is_strict:
            raise ValueError("A user-supplied vocabulary is never written by the model")
        if (
            self.change_request is not None
            and self.change_request.based_on_version >= self.version
        ):
            raise ValueError("ReviewPlan.change_request.based_on_version must come before it")

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    def accepted(self, by: UUID, at: datetime) -> ReviewPlan:
        """Accept and freeze this version. A version is accepted once."""
        if self.is_accepted:
            raise PlanConflictError(
                "Someone else already accepted this draft. Showing the latest."
            )
        return replace(
            self, vocabulary=self.vocabulary.freeze(at=at), accepted_at=at, accepted_by=by
        )

    def followed_by(
        self,
        *,
        concepts: tuple[Concept, ...],
        criteria: tuple[Criterion, ...],
        origin: VocabularyOrigin,
        author: PlanAuthor,
        at: datetime,
        clarifications: tuple[ClarifyingQuestion, ...] | None = None,
        change_request: ChangeRequest | None = None,
        notes: tuple[str, ...] = (),
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> ReviewPlan:
        """The next version. This one is left exactly as it is."""
        return first_plan(
            self.review_id,
            concepts=concepts,
            criteria=criteria,
            origin=origin,
            author=author,
            at=at,
            version=self.version + 1,
            clarifications=self.clarifications if clarifications is None else clarifications,
            change_request=change_request,
            notes=notes,
            model_id=model_id,
            prompt_version=prompt_version,
        )


def first_plan(
    review_id: UUID,
    *,
    concepts: tuple[Concept, ...],
    criteria: tuple[Criterion, ...],
    origin: VocabularyOrigin,
    author: PlanAuthor,
    at: datetime,
    version: int = 1,
    clarifications: tuple[ClarifyingQuestion, ...] = (),
    change_request: ChangeRequest | None = None,
    notes: tuple[str, ...] = (),
    model_id: str | None = None,
    prompt_version: str | None = None,
) -> ReviewPlan:
    return ReviewPlan(
        review_id=review_id,
        version=version,
        vocabulary=Vocabulary(concepts=concepts, origin=origin, version=version),
        criteria=criteria,
        author=author,
        created_at=at,
        clarifications=clarifications,
        change_request=change_request,
        notes=notes,
        model_id=model_id,
        prompt_version=prompt_version,
    )


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class ConceptChange:
    theme: str
    term: str
    change: ChangeKind
    by: PlanAuthor


@dataclass(frozen=True, slots=True)
class TermChange:
    """One term added to or removed from a concept, and who made the version that did it."""

    theme: str
    concept: str
    term: str
    change: ChangeKind
    by: PlanAuthor


@dataclass(frozen=True, slots=True)
class CriterionChange:
    kind: CriterionKind
    text: str
    change: ChangeKind
    by: PlanAuthor


@dataclass(frozen=True, slots=True)
class PlanDiff:
    """What changed from one version to the next: the draft canvas's "compare with" view.

    `from_version` is `None` for the first version, against which everything is new.
    """

    from_version: int | None
    to_version: int
    concepts: tuple[ConceptChange, ...] = ()
    terms: tuple[TermChange, ...] = ()
    criteria: tuple[CriterionChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.concepts or self.terms or self.criteria)


type TermKey = tuple[str, str, str]
"""(theme, concept, term), each case-folded: how a term is recognised across versions."""


def _concept_key(concept: Concept) -> tuple[str, str]:
    return (concept.theme.strip().casefold(), concept.term.strip().casefold())


def _term_entries(plan: ReviewPlan) -> dict[TermKey, tuple[Concept, str]]:
    entries: dict[TermKey, tuple[Concept, str]] = {}
    for concept in plan.vocabulary.concepts:
        theme, term = _concept_key(concept)
        for text in concept.terms:
            entries.setdefault((theme, term, text.casefold()), (concept, text))
    return entries


def _criterion_key(criterion: Criterion) -> tuple[CriterionKind, str]:
    return (criterion.kind, criterion.text.casefold())


def diff_plans(before: ReviewPlan | None, after: ReviewPlan) -> PlanDiff:
    """Concepts, terms and criteria added or removed, credited to whoever wrote `after`.

    Matching ignores case, as searching does. A concept moved to another theme
    shows as removed from one and added to the other.
    """
    by = after.author
    old_concepts = (
        {} if before is None else {_concept_key(c): c for c in before.vocabulary.concepts}
    )
    new_concepts = {_concept_key(c): c for c in after.vocabulary.concepts}
    old_terms = {} if before is None else _term_entries(before)
    new_terms = _term_entries(after)
    old_criteria = {} if before is None else {_criterion_key(c): c for c in before.criteria}
    new_criteria = {_criterion_key(c): c for c in after.criteria}

    def concept_change(concept: Concept, change: ChangeKind) -> ConceptChange:
        return ConceptChange(concept.theme, concept.term, change, by)

    def term_change(entry: tuple[Concept, str], change: ChangeKind) -> TermChange:
        concept, text = entry
        return TermChange(concept.theme, concept.term, text, change, by)

    def criterion_change(criterion: Criterion, change: ChangeKind) -> CriterionChange:
        return CriterionChange(criterion.kind, criterion.text, change, by)

    added, removed = ChangeKind.ADDED, ChangeKind.REMOVED
    return PlanDiff(
        from_version=None if before is None else before.version,
        to_version=after.version,
        concepts=(
            *(
                concept_change(c, added)
                for k, c in new_concepts.items()
                if k not in old_concepts
            ),
            *(
                concept_change(c, removed)
                for k, c in old_concepts.items()
                if k not in new_concepts
            ),
        ),
        terms=(
            *(term_change(e, added) for k, e in new_terms.items() if k not in old_terms),
            *(term_change(e, removed) for k, e in old_terms.items() if k not in new_terms),
        ),
        criteria=(
            *(
                criterion_change(c, added)
                for k, c in new_criteria.items()
                if k not in old_criteria
            ),
            *(
                criterion_change(c, removed)
                for k, c in old_criteria.items()
                if k not in new_criteria
            ),
        ),
    )


type RedoStage = Literal["queries", "screen"]


def first_stage_to_redo(before: ReviewPlan, after: ReviewPlan) -> RedoStage | None:
    """The earliest pipeline stage a change from `before` to `after` affects, or `None`.

    A changed search starts again from `queries`; changed criteria alone redo `screen`.
    """
    diff = diff_plans(before, after)
    if diff.concepts or diff.terms:
        return "queries"
    if diff.criteria:
        return "screen"
    return None


def term_authors(versions: Sequence[ReviewPlan]) -> dict[TermKey, PlanAuthor]:
    """Who added each term of the latest version: the author of the version that brought it.

    Walks the versions in order, so a term dropped and later added back belongs
    to whoever added it back. Built from recorded authors only.
    """
    authors: dict[TermKey, PlanAuthor] = {}
    for plan in sorted(versions, key=lambda p: p.version):
        present = _term_entries(plan)
        authors = {key: authors.get(key, plan.author) for key in present}
    return authors


def term_key(concept: Concept, term: str) -> TermKey:
    theme, concept_term = _concept_key(concept)
    return (theme, concept_term, term.casefold())


class VocabularyFormat(StrEnum):
    """The two documented upload formats for a strict vocabulary (route B)."""

    TEXT = "text"
    CSV = "csv"


_CSV_HEADER = ("theme", "term", "synonyms")


def parse_strict_vocabulary(content: str, format: VocabularyFormat) -> tuple[Concept, ...]:
    """Parse a user's own vocabulary by code: no model, nothing added or respelled.

    The text and CSV formats are documented in `docs/api.md`. A bad line is refused with
    its number, never silently dropped.
    """
    rows = _text_rows(content) if format is VocabularyFormat.TEXT else _csv_rows(content)
    concepts: list[Concept] = []
    seen: set[tuple[str, str]] = set()
    for line, theme, terms in rows:
        concept = _strict_concept(line, theme, terms)
        key = _concept_key(concept)
        if key in seen:
            raise ValueError(
                f"line {line}: '{concept.term}' is already a concept in this theme"
            )
        seen.add(key)
        concepts.append(concept)
    if not concepts:
        raise ValueError("The vocabulary has no terms")
    if len(concepts) > MAX_CONCEPTS:
        raise ValueError(f"The vocabulary has more than {MAX_CONCEPTS} concepts")
    return tuple(concepts)


def _text_rows(content: str) -> Iterable[tuple[int, str, list[str]]]:
    theme = DEFAULT_THEME
    for number, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            theme = line[1:-1].strip()
            if not theme:
                raise ValueError(f"line {number}: a theme needs a name")
            continue
        yield number, theme, line.split(";")


def _csv_rows(content: str) -> Iterable[tuple[int, str, list[str]]]:
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if header is None or tuple(h.strip().casefold() for h in header) != _CSV_HEADER:
        raise ValueError("The CSV needs the header row: theme,term,synonyms")
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        number = reader.line_num
        if len(row) != len(_CSV_HEADER):
            raise ValueError(f"line {number}: expected 3 columns, found {len(row)}")
        theme, term, synonyms = row
        listed = synonyms.split(";") if synonyms.strip() else []
        yield number, theme.strip() or DEFAULT_THEME, [term, *listed]


def _strict_concept(line: int, theme: str, raw_terms: list[str]) -> Concept:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = raw.strip()
        if not term:
            raise ValueError(f"line {line}: an empty term")
        if len(term) > MAX_TERM_LENGTH:
            raise ValueError(f"line {line}: a term is longer than {MAX_TERM_LENGTH} characters")
        if _has_hidden_characters(term, frozenset()):
            raise ValueError(f"line {line}: a term contains a control character")
        if term.casefold() in seen:
            raise ValueError(f"line {line}: '{term}' appears twice in one concept")
        seen.add(term.casefold())
        terms.append(term)
    if len(terms) > MAX_TERMS_PER_CONCEPT:
        raise ValueError(f"line {line}: more than {MAX_TERMS_PER_CONCEPT} terms")
    return Concept(term=terms[0], synonyms=tuple(terms[1:]), theme=theme)


def revision_from_gate(
    revisions: Mapping[str, JsonValue],
) -> tuple[tuple[Concept, ...] | None, tuple[Criterion, ...] | None]:
    """Read what a person changed at the vocabulary gate: concepts, criteria, or both.

    Shape: `{"vocabulary": {"concepts": [{term, synonyms, theme}]}, "criteria":
    [{kind, text}]}`. `None` for a part that was not changed.
    """
    vocabulary = revisions.get("vocabulary")
    criteria = revisions.get("criteria")
    if vocabulary is None and criteria is None:
        raise ValueError("The gate's revisions change neither the vocabulary nor the criteria")
    concepts = None if vocabulary is None else _concepts_from_json(vocabulary)
    return concepts, None if criteria is None else criteria_from_json(criteria)


def _text_list(value: JsonValue, what: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list of text")
    texts: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{what} must be a list of text")
        texts.append(item.strip())
    return tuple(texts)


def _concepts_from_json(vocabulary: JsonValue) -> tuple[Concept, ...]:
    items = vocabulary.get("concepts") if isinstance(vocabulary, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("revisions.vocabulary needs a list of concepts")
    concepts: list[Concept] = []
    for item in items:
        term = item.get("term") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(term, str):
            raise ValueError("Each revised concept needs a term")
        theme = item.get("theme", DEFAULT_THEME)
        if not isinstance(theme, str):
            raise ValueError("A revised concept's theme must be text")
        synonyms = _text_list(item.get("synonyms", []), "A revised concept's synonyms")
        concepts.append(Concept(term.strip(), synonyms, theme.strip()))
    return tuple(concepts)


def criteria_from_json(criteria: JsonValue) -> tuple[Criterion, ...]:
    """`[{kind, text}]` into numbered criteria; `ValueError` for anything else."""
    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")
    entries: list[tuple[CriterionKind, str]] = []
    for item in criteria:
        kind = item.get("kind") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if (
            not isinstance(kind, str)
            or kind not in _CRITERION_KINDS
            or not isinstance(text, str)
        ):
            raise ValueError("Each criterion needs a kind (inclusion or exclusion) and a text")
        entries.append((CriterionKind(kind), text))
    return number_criteria(entries)


_CRITERION_KINDS = frozenset(k.value for k in CriterionKind)


def refuse_rule_terms(fields: Iterable[tuple[str, str]], rules: Sequence[MaskingRule]) -> None:
    """Refuse plan text that holds a term the project always masks.

    Plan text reaches the model and the paper sources as written, so a person learns
    when they write it. `fields` pairs a field's name with its text; the `ValueError`
    names the first field that holds one, never the term.
    """
    for name, text in fields:
        if rule_spans(text, rules):
            raise ValueError(
                f"{name} holds a term this project always masks. Remove it or reword it."
            )


def plan_text_fields(
    concepts: Sequence[Concept], criteria: Sequence[Criterion]
) -> Iterator[tuple[str, str]]:
    """Every piece of a plan's text, named as `refuse_rule_terms` reports it."""
    for criterion in criteria:
        yield f"Criterion {criterion.id}", criterion.text
    for number, concept in enumerate(concepts, start=1):
        for text in (concept.theme, *concept.terms):
            yield f"Concept {number}", text


def revision_text_fields(
    value: JsonValue, path: str = "revisions"
) -> Iterator[tuple[str, str]]:
    """Every text in a gate's revisions, named by its path: `revisions.criteria[1].text`.

    Read whole rather than by `revision_from_gate`, so text in a shape it would
    reject is checked too.
    """
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from revision_text_fields(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from revision_text_fields(item, f"{path}.{key}")


class ModelCallPurpose(StrEnum):
    CLARIFY = "clarify"
    DRAFT = "draft"
    REVISE = "revise"


class SentTextKind(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    CHANGE_REQUEST = "change_request"
    CURRENT_PLAN = "current_plan"


@dataclass(frozen=True, slots=True)
class SentText:
    """One piece of text exactly as a model received it.

    A person's question, answers and change request are masked. The current plan is
    sent as written, as JSON: its text was checked against the project's rules instead.
    """

    kind: SentTextKind
    sent: str
    counts: Mapping[str, int] = field(default_factory=dict[str, int])
    ref: str | None = None

    @property
    def masked_total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True, slots=True)
class PrivacyReceipt:
    """What one model call received of the people's text, for the "Sent as" receipt.

    Written once the text may have left, whether or not the call succeeded: a failed call
    can still have been read. A call the prompt guard refused sent nothing and has none.
    """

    id: UUID
    review_id: UUID
    project_id: UUID
    purpose: ModelCallPurpose
    created_at: datetime
    created_by: UUID | None
    model_id: str
    prompt_version: str
    is_delivered: bool
    items: tuple[SentText, ...]
    plan_version: int | None = None
    change_request_id: UUID | None = None

    def __post_init__(self) -> None:
        require_aware("PrivacyReceipt", created_at=self.created_at)
        _require_receipt_links("PrivacyReceipt", self.plan_version)

    @property
    def masked_total(self) -> int:
        return sum(item.masked_total for item in self.items)


@dataclass(frozen=True, slots=True)
class StoredConversation:
    """A `PlanConversation` as stored: its content sealed under the project's key."""

    review_id: UUID
    project_id: UUID
    mode: PlanMode
    created_at: datetime
    created_by: UUID | None
    body_sealed: bytes
    version: int = 0

    def __post_init__(self) -> None:
        require_aware("StoredConversation", created_at=self.created_at)
        if not self.body_sealed:
            raise ValueError("StoredConversation.body_sealed must not be empty")


@dataclass(frozen=True, slots=True)
class StoredPlanVersion:
    """A `ReviewPlan` as stored: what it says sealed, who and when in the clear."""

    review_id: UUID
    project_id: UUID
    version: int
    origin: VocabularyOrigin
    author: PlanAuthor
    created_at: datetime
    body_sealed: bytes
    accepted_at: datetime | None = None
    accepted_by: UUID | None = None

    def __post_init__(self) -> None:
        require_aware(
            "StoredPlanVersion", created_at=self.created_at, accepted_at=self.accepted_at
        )
        if self.version < 1:
            raise ValueError("StoredPlanVersion.version starts at 1")
        if (self.accepted_at is None) != (self.accepted_by is None):
            raise ValueError("StoredPlanVersion.accepted_at and accepted_by are set together")
        if not self.body_sealed:
            raise ValueError("StoredPlanVersion.body_sealed must not be empty")

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None


@dataclass(frozen=True, slots=True)
class StoredReceipt:
    """A `PrivacyReceipt` as stored: the masked text sealed, the rest in the clear."""

    id: UUID
    review_id: UUID
    project_id: UUID
    purpose: ModelCallPurpose
    created_at: datetime
    created_by: UUID | None
    model_id: str
    prompt_version: str
    is_delivered: bool
    body_sealed: bytes
    plan_version: int | None = None
    change_request_id: UUID | None = None

    def __post_init__(self) -> None:
        require_aware("StoredReceipt", created_at=self.created_at)
        if not self.body_sealed:
            raise ValueError("StoredReceipt.body_sealed must not be empty")
        _require_receipt_links("StoredReceipt", self.plan_version)


def _require_receipt_links(owner: str, plan_version: int | None) -> None:
    if plan_version is not None and plan_version < 1:
        raise ValueError(f"{owner}.plan_version starts at 1")


class PlanSource[UnitOfWorkT](Protocol):
    """How a stage reads the plan a run uses, inside the caller's unit of work."""

    async def accepted_plan(self, uow: UnitOfWorkT, review_id: UUID) -> ReviewPlan | None:
        """The review's most recently accepted version, or `None` if none was accepted.

        A newer draft does not replace it until it is accepted itself. Planning
        is refused while a run is going, so a run sees one plan from start to end.
        """
        ...
