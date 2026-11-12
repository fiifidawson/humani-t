"""Screening: what was decided about one paper, why, by what, and how a run's papers add up.

A `ScreeningDecision` answers "why was this paper included or excluded?" months later: it
names the model, prompt and criteria version that produced it, and an exclusion always
carries its criteria, so PRISMA can count exclusions by reason.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self
from uuid import UUID

from app.domain.discovery import TextKind
from app.domain.runs import JsonValue

MAX_RATIONALE_LENGTH = 2000
MAX_QUOTE_LENGTH = 300

UNREADABLE_RATIONALE = (
    "The model's answer could not be read, so this paper needs a person to decide."
)


class ScreeningLabel(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNCERTAIN = "uncertain"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScreeningTier(StrEnum):
    """The names people see, derived from label and priority."""

    HIGHLY_RELEVANT = "highly_relevant"
    RELEVANT = "relevant"
    POSSIBLY_RELEVANT = "possibly_relevant"
    EXCLUDED = "excluded"
    UNCERTAIN = "uncertain"


_INCLUDED_TIERS: Mapping[Priority, ScreeningTier] = {
    Priority.HIGH: ScreeningTier.HIGHLY_RELEVANT,
    Priority.MEDIUM: ScreeningTier.RELEVANT,
    Priority.LOW: ScreeningTier.POSSIBLY_RELEVANT,
}


def tier_of(label: ScreeningLabel, priority: Priority) -> ScreeningTier:
    """The name people see: an inclusion by its priority; any other label by itself."""
    if label is ScreeningLabel.INCLUDE:
        return _INCLUDED_TIERS[priority]
    if label is ScreeningLabel.EXCLUDE:
        return ScreeningTier.EXCLUDED
    return ScreeningTier.UNCERTAIN


class CriterionVerdict(StrEnum):
    """How far a paper meets one criterion. `yes` and `partly` count as met."""

    YES = "yes"
    PARTLY = "partly"
    NO = "no"
    UNCLEAR = "unclear"


_MET = frozenset({CriterionVerdict.YES, CriterionVerdict.PARTLY})
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CriterionAnswer:
    """One criterion's answer for one paper, and the words of the paper it rests on.

    `quote` is cut from the masked text the model read, never written by it:
    `check_quotes` refuses any other. `None` means the model quoted nothing.
    """

    criterion_id: str
    verdict: CriterionVerdict
    quote: str | None = None

    def __post_init__(self) -> None:
        if not self.criterion_id.strip():
            raise ValueError("CriterionAnswer.criterion_id must not be blank")
        if self.quote is not None:
            if not self.quote.strip():
                raise ValueError("CriterionAnswer.quote must not be blank; use None")
            if len(self.quote) > MAX_QUOTE_LENGTH:
                raise ValueError(
                    f"CriterionAnswer.quote must be at most {MAX_QUOTE_LENGTH} characters"
                )

    @property
    def is_met(self) -> bool:
        return self.verdict in _MET


def check_quotes(answers: Sequence[CriterionAnswer], text: str) -> None:
    """Refuse, with `ValueError`, any quote that is not a piece of `text`.

    Runs of whitespace count as one space, since a model may rejoin a line break. Any other
    difference would show a reviewer the model's words as the paper's.
    """
    haystack = _SPACES.sub(" ", text).strip()
    for answer in answers:
        if answer.quote is None:
            continue
        if _SPACES.sub(" ", answer.quote).strip() not in haystack:
            raise ValueError(
                f"the quote for {answer.criterion_id} is not in the text the model was sent"
            )


def exclusion_reasons(
    label: ScreeningLabel,
    criteria_matched: Collection[str],
    inclusion_ids: Sequence[str],
    exclusion_ids: Sequence[str],
) -> tuple[str, ...]:
    """The criteria that exclude a paper, or `ValueError` if the label contradicts them.

    An inclusion must trigger no exclusion criterion. An exclusion's reasons are the exclusion
    criteria it triggers, then the inclusion criteria it misses; it needs at least one.
    """
    known = set(inclusion_ids) | set(exclusion_ids)
    unknown = sorted(set(criteria_matched) - known)
    if unknown:
        raise ValueError(f"the answer names unknown criteria: {', '.join(unknown)}")
    triggered = tuple(c for c in exclusion_ids if c in criteria_matched)
    if label is ScreeningLabel.INCLUDE:
        if triggered:
            raise ValueError("an included paper cannot meet an exclusion criterion")
        return ()
    if label is ScreeningLabel.UNCERTAIN:
        return ()
    missed = tuple(c for c in inclusion_ids if c not in criteria_matched)
    reasons = triggered + missed
    if not reasons:
        raise ValueError(
            "a paper excluded while meeting every inclusion criterion and no exclusion "
            "criterion has no reason to be excluded"
        )
    return reasons


@dataclass(frozen=True, slots=True)
class ScreeningDecision:
    """One run's assessment of one paper, with what produced it.

    `evidence_kind` says what was read: the abstract, never yet the full text. Decisions made
    before per-criterion answers existed have no `criterion_answers`.
    """

    run_id: UUID
    paper_id: UUID
    label: ScreeningLabel
    score: float
    priority: Priority
    rationale: str
    criteria_matched: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    model_id: str
    prompt_version: str
    criteria_version: int
    evidence_kind: TextKind
    decided_at: datetime
    is_unreadable: bool = False
    criterion_answers: tuple[CriterionAnswer, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("ScreeningDecision.score must be between 0 and 1")
        if not self.rationale.strip():
            raise ValueError("ScreeningDecision.rationale must not be blank")
        if len(self.rationale) > MAX_RATIONALE_LENGTH:
            raise ValueError(
                f"ScreeningDecision.rationale must be at most {MAX_RATIONALE_LENGTH} characters"
            )
        _require_distinct("criteria_matched", self.criteria_matched)
        _require_distinct("exclusion_reasons", self.exclusion_reasons)
        if (self.label is ScreeningLabel.EXCLUDE) != bool(self.exclusion_reasons):
            raise ValueError(
                "ScreeningDecision.exclusion_reasons is set for an exclusion, and only then"
            )
        if self.is_unreadable and self.label is not ScreeningLabel.UNCERTAIN:
            raise ValueError("ScreeningDecision.is_unreadable belongs to an uncertain decision")
        if not self.model_id.strip():
            raise ValueError("ScreeningDecision.model_id must not be blank")
        if not self.prompt_version.strip():
            raise ValueError("ScreeningDecision.prompt_version must not be blank")
        if self.criteria_version < 1:
            raise ValueError("ScreeningDecision.criteria_version starts at 1")
        if self.decided_at.tzinfo is None:
            raise ValueError("ScreeningDecision.decided_at must be timezone-aware")
        _require_agreeing_answers(self.criterion_answers, self.criteria_matched)

    @classmethod
    def unreadable(
        cls,
        *,
        run_id: UUID,
        paper_id: UUID,
        model_id: str,
        prompt_version: str,
        criteria_version: int,
        evidence_kind: TextKind,
        decided_at: datetime,
    ) -> Self:
        """What is recorded when the model answered twice and neither answer could be read."""
        return cls(
            run_id=run_id,
            paper_id=paper_id,
            label=ScreeningLabel.UNCERTAIN,
            score=0.0,
            priority=Priority.LOW,
            rationale=UNREADABLE_RATIONALE,
            criteria_matched=(),
            exclusion_reasons=(),
            model_id=model_id,
            prompt_version=prompt_version,
            criteria_version=criteria_version,
            evidence_kind=evidence_kind,
            decided_at=decided_at,
            is_unreadable=True,
        )

    @property
    def tier(self) -> ScreeningTier:
        return tier_of(self.label, self.priority)


@dataclass(frozen=True, slots=True)
class StoredDecision:
    """A `ScreeningDecision` as stored: the rationale and per-criterion answers sealed.

    Both are model output about a paper, so they are project data. Labels, scores and
    criterion ids stay in the clear so a run's papers can be counted without opening rows.
    """

    run_id: UUID
    paper_id: UUID
    project_id: UUID
    label: ScreeningLabel
    priority: Priority
    score: float
    criteria_matched: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    model_id: str
    prompt_version: str
    criteria_version: int
    evidence_kind: TextKind
    is_unreadable: bool
    decided_at: datetime
    rationale_sealed: bytes
    answers_sealed: bytes | None = None

    def __post_init__(self) -> None:
        if not self.rationale_sealed:
            raise ValueError("StoredDecision.rationale_sealed must not be empty")
        if self.answers_sealed is not None and not self.answers_sealed:
            raise ValueError("StoredDecision.answers_sealed must not be empty; use None")

    @property
    def tier(self) -> ScreeningTier:
        return tier_of(self.label, self.priority)

    @classmethod
    def sealed(
        cls,
        decision: ScreeningDecision,
        project_id: UUID,
        rationale_sealed: bytes,
        answers_sealed: bytes | None = None,
    ) -> Self:
        return cls(
            run_id=decision.run_id,
            paper_id=decision.paper_id,
            project_id=project_id,
            label=decision.label,
            priority=decision.priority,
            score=decision.score,
            criteria_matched=decision.criteria_matched,
            exclusion_reasons=decision.exclusion_reasons,
            model_id=decision.model_id,
            prompt_version=decision.prompt_version,
            criteria_version=decision.criteria_version,
            evidence_kind=decision.evidence_kind,
            is_unreadable=decision.is_unreadable,
            decided_at=decision.decided_at,
            rationale_sealed=rationale_sealed,
            answers_sealed=answers_sealed,
        )

    def opened(
        self, rationale: str, criterion_answers: tuple[CriterionAnswer, ...] = ()
    ) -> ScreeningDecision:
        """The decision with its rationale in the clear; checked again as it is rebuilt."""
        return ScreeningDecision(
            run_id=self.run_id,
            paper_id=self.paper_id,
            label=self.label,
            score=self.score,
            priority=self.priority,
            rationale=rationale,
            criteria_matched=self.criteria_matched,
            exclusion_reasons=self.exclusion_reasons,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            criteria_version=self.criteria_version,
            evidence_kind=self.evidence_kind,
            decided_at=self.decided_at,
            is_unreadable=self.is_unreadable,
            criterion_answers=criterion_answers,
        )


def _require_agreeing_answers(
    answers: tuple[CriterionAnswer, ...], criteria_matched: tuple[str, ...]
) -> None:
    if not answers:
        return
    ids = [a.criterion_id for a in answers]
    if len(set(ids)) != len(ids):
        raise ValueError("ScreeningDecision.criterion_answers must answer each criterion once")
    if {a.criterion_id for a in answers if a.is_met} != set(criteria_matched):
        raise ValueError(
            "ScreeningDecision.criterion_answers must agree with criteria_matched: "
            "yes or partly exactly for the criteria met"
        )


def screened_item(
    paper_id: UUID,
    *,
    decision: ScreeningDecision | None,
    title: str | None,
    reason: str,
) -> dict[str, JsonValue]:
    """The screen stage's live row for one paper: decided, or failed when `decision` is None.

    `title` and `reason` are shown to every member, so they must be masked
    text: a line of what the model read, and of what it answered.
    """
    if not reason.strip():
        raise ValueError("a screened row needs a reason")
    return {
        "paper_id": str(paper_id),
        "outcome": "failed" if decision is None else "screened",
        "label": None if decision is None else decision.label.value,
        "priority": None if decision is None else decision.priority.value,
        "tier": None if decision is None else decision.tier.value,
        "title": title,
        "reason": reason,
    }


def _require_distinct(name: str, values: tuple[str, ...]) -> None:
    if any(not v.strip() for v in values):
        raise ValueError(f"ScreeningDecision.{name} must not contain blank ids")
    if len(set(values)) != len(values):
        raise ValueError(f"ScreeningDecision.{name} must not name a criterion twice")


class PassageSourceKind(StrEnum):
    PAPER = "paper"
    DOCUMENT = "document"


def passage_tag(position: int) -> str:
    """The tag a brief cites a paper by: its place in the run's papers, `P1`, `P2`, …

    Taken from the place, not the order of inclusion, so a tag never moves when another
    paper's label changes.
    """
    if position < 1:
        raise ValueError("a passage tag needs a position counting from 1")
    return f"P{position}"


@dataclass(frozen=True, slots=True)
class Passage:
    """Masked text the brief may cite, and where it came from.

    `paper_id` is the paper's id, or the document's for a team document.
    `locator` says which part of the source the text is (`abstract`).
    """

    tag: str
    paper_id: UUID
    source_kind: PassageSourceKind
    text: str
    locator: str

    def __post_init__(self) -> None:
        if not self.tag.strip():
            raise ValueError("Passage.tag must not be blank")
        if not self.text.strip():
            raise ValueError("Passage.text must not be blank")
        if not self.locator.strip():
            raise ValueError("Passage.locator must not be blank")


class EvidenceSource[UnitOfWorkT](Protocol):
    """Where the brief stage reads the screened-in evidence of a run."""

    async def included_passages(
        self, uow: UnitOfWorkT, review_id: UUID, run_id: UUID
    ) -> tuple[Passage, ...]:
        """Masked passages of the run's included papers, in tag order. Empty if none."""
        ...


class MaskedTextSource[UnitOfWorkT](Protocol):
    """Where the screen stage reads a paper's masked text; the mask stage writes it."""

    async def masked_text(
        self, uow: UnitOfWorkT, project_id: UUID, paper_id: UUID
    ) -> str | None:
        """The paper's kept text with personal data masked, or `None` if not masked yet.

        `None` is normal: the paper is then not sent to a model at all.
        """
        ...


class _Decided(Protocol):
    @property
    def label(self) -> ScreeningLabel: ...

    @property
    def exclusion_reasons(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class PrismaCounts:
    """PRISMA-style flow for one run: identified, deduplicated, screened, decided."""

    identified_by_source: Mapping[str, int]
    failed_sources: tuple[str, ...]
    after_deduplication: int
    screened: int
    included: int
    excluded: int
    uncertain: int
    excluded_by_reason: Mapping[str, int]

    @property
    def identified(self) -> int:
        return sum(self.identified_by_source.values())

    @property
    def duplicates_removed(self) -> int:
        return self.identified - self.after_deduplication

    @property
    def not_screened(self) -> int:
        """Papers with no decision: skipped (no abstract, not masked) or failed."""
        return self.after_deduplication - self.screened


def prisma_counts(
    *,
    identified_by_source: Mapping[str, int],
    failed_sources: Sequence[str],
    after_deduplication: int,
    decisions: Sequence[_Decided],
) -> PrismaCounts:
    identified = sum(identified_by_source.values())
    if not 0 <= after_deduplication <= identified:
        raise ValueError(
            "PRISMA after_deduplication must be between 0 and the papers identified"
        )
    if len(decisions) > after_deduplication:
        raise ValueError("PRISMA cannot have more decisions than papers after deduplication")
    labels = Counter(d.label for d in decisions)
    reasons: Counter[str] = Counter(
        reason
        for d in decisions
        if d.label is ScreeningLabel.EXCLUDE
        for reason in d.exclusion_reasons
    )
    return PrismaCounts(
        identified_by_source=MappingProxyType(dict(identified_by_source)),
        failed_sources=tuple(failed_sources),
        after_deduplication=after_deduplication,
        screened=len(decisions),
        included=labels[ScreeningLabel.INCLUDE],
        excluded=labels[ScreeningLabel.EXCLUDE],
        uncertain=labels[ScreeningLabel.UNCERTAIN],
        excluded_by_reason=MappingProxyType(dict(sorted(reasons.items()))),
    )
