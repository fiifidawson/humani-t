"""Label rules: how a paper's label follows from the per-criterion answers the model gave.

The model's `ScreeningDecision` is never changed. A label under rules is computed when it
is read, from the stored answers and the review's latest `LabelRulesVersion` (ADR 0013).
Reading the rules and the plan's criteria belongs in `application/label_rules.py`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.plans import Criterion, CriterionKind
from app.domain.screening import (
    CriterionVerdict,
    Priority,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
    tier_of,
)
from app.domain.timestamps import require_aware


class UnclearCountsAs(StrEnum):
    """What an `unclear` answer to an inclusion criterion counts as."""

    MET = "met"
    NOT_MET = "not_met"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LabelRules:
    """How many inclusion criteria Possibly relevant needs, and what `unclear` counts as."""

    possibly_min_met: int
    unclear_counts_as: UnclearCountsAs

    def __post_init__(self) -> None:
        if isinstance(self.possibly_min_met, bool) or self.possibly_min_met < 1:
            raise ValueError("LabelRules.possibly_min_met must be at least 1")
        if self.unclear_counts_as not in tuple(UnclearCountsAs):
            raise ValueError("LabelRules.unclear_counts_as must be met, not_met or uncertain")


@dataclass(frozen=True, slots=True)
class RuledLabel:
    """The label, priority and exclusion reasons a decision's answers get under rules."""

    label: ScreeningLabel
    priority: Priority
    exclusion_reasons: tuple[str, ...] = ()

    @property
    def tier(self) -> ScreeningTier:
        return tier_of(self.label, self.priority)


def label_under(
    rules: LabelRules, decision: ScreeningDecision, kinds: Mapping[str, CriterionKind]
) -> RuledLabel | None:
    """The decision's label under `rules`, from its answers to the criteria in `kinds`.

    `None` when the rules cannot reach it: no per-criterion answers, an unreadable answer,
    or answers that are not exactly the criteria in `kinds`. It then keeps the model's label.
    An `unclear` counted as met is met but not `yes`, so never Highly relevant.
    """
    answers = decision.criterion_answers
    if decision.is_unreadable or not answers:
        return None
    if {a.criterion_id for a in answers} != set(kinds):
        return None
    inclusion = [a for a in answers if kinds[a.criterion_id] is CriterionKind.INCLUSION]
    # Only `yes` excludes: a partly-met exclusion criterion should not silently drop a paper.
    triggered = tuple(
        a.criterion_id
        for a in answers
        if kinds[a.criterion_id] is CriterionKind.EXCLUSION
        and a.verdict is CriterionVerdict.YES
    )
    is_unclear_met = rules.unclear_counts_as is UnclearCountsAs.MET
    missed = tuple(
        a.criterion_id
        for a in inclusion
        if not (a.is_met or (a.verdict is CriterionVerdict.UNCLEAR and is_unclear_met))
    )
    if triggered:
        return RuledLabel(ScreeningLabel.EXCLUDE, Priority.LOW, triggered + missed)
    if all(a.verdict is CriterionVerdict.YES for a in inclusion):
        return RuledLabel(ScreeningLabel.INCLUDE, Priority.HIGH)
    if not missed:
        return RuledLabel(ScreeningLabel.INCLUDE, Priority.MEDIUM)
    if rules.unclear_counts_as is UnclearCountsAs.UNCERTAIN and any(
        a.verdict is CriterionVerdict.UNCLEAR for a in inclusion
    ):
        return RuledLabel(ScreeningLabel.UNCERTAIN, Priority.LOW)
    if len(inclusion) - len(missed) >= rules.possibly_min_met:
        return RuledLabel(ScreeningLabel.INCLUDE, Priority.LOW)
    return RuledLabel(ScreeningLabel.EXCLUDE, Priority.LOW, missed)


@dataclass(frozen=True, slots=True)
class TierMove:
    """How many papers a change of rules moves from one tier to another."""

    from_tier: ScreeningTier
    to_tier: ScreeningTier
    count: int


def count_moves(changes: Iterable[tuple[ScreeningTier, ScreeningTier]]) -> tuple[TierMove, ...]:
    """Papers per (before, after) pair, most relevant first; unmoved papers are left out."""
    counted = Counter((before, after) for before, after in changes if before is not after)
    order = list(ScreeningTier)
    return tuple(
        TierMove(before, after, counted[before, after])
        for before, after in sorted(
            counted, key=lambda m: (order.index(m[0]), order.index(m[1]))
        )
    )


@dataclass(frozen=True, slots=True)
class LabelRulesVersion:
    """One version of a review's label rules, as applied: written once, never changed.

    `papers_moved` is how many of the review's newest run's papers changed tier when
    it was applied: the number the chat's note shows.
    """

    review_id: UUID
    project_id: UUID
    version: int
    rules: LabelRules
    created_by: UUID
    created_at: datetime
    papers_moved: int

    def __post_init__(self) -> None:
        require_aware("LabelRulesVersion", created_at=self.created_at)
        if self.version < 1:
            raise ValueError("LabelRulesVersion.version starts at 1")
        if self.papers_moved < 0:
            raise ValueError("LabelRulesVersion.papers_moved must not be negative")


class CriteriaSource[UnitOfWorkT](Protocol):
    """Where the rules read the criteria of the plan version a decision was made with."""

    async def criteria(
        self, uow: UnitOfWorkT, review_id: UUID, version: int
    ) -> tuple[Criterion, ...] | None:
        """The criteria of that plan version, or `None` if the review has no such version."""
        ...
