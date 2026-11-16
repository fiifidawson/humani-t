"""Requirements for label rules: a paper's label from its per-criterion answers, with no I/O.

The model's decision is never changed. The rules only compute a label from the answers it
stored, so a decision without answers, or one the model's answer could not be read for,
keeps the model's label.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.discovery import TextKind
from app.domain.label_rules import (
    LabelRules,
    LabelRulesVersion,
    TierMove,
    UnclearCountsAs,
    count_moves,
    label_under,
)
from app.domain.plans import CriterionKind
from app.domain.screening import (
    CriterionAnswer,
    CriterionVerdict,
    Priority,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
)

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
YES, PARTLY, NO, UNCLEAR = (
    CriterionVerdict.YES,
    CriterionVerdict.PARTLY,
    CriterionVerdict.NO,
    CriterionVerdict.UNCLEAR,
)
KINDS: Mapping[str, CriterionKind] = {
    "I1": CriterionKind.INCLUSION,
    "I2": CriterionKind.INCLUSION,
    "I3": CriterionKind.INCLUSION,
    "E1": CriterionKind.EXCLUSION,
}
DEFAULT = LabelRules(possibly_min_met=1, unclear_counts_as=UnclearCountsAs.NOT_MET)


def decided(i1: CriterionVerdict, i2: CriterionVerdict, i3: CriterionVerdict, e1=NO):
    """The model's decision for these answers; its own label is deliberately `uncertain`."""
    verdicts = {"I1": i1, "I2": i2, "I3": i3, "E1": e1}
    return ScreeningDecision(
        run_id=uuid.uuid4(),
        paper_id=uuid.uuid4(),
        label=ScreeningLabel.UNCERTAIN,
        score=0.5,
        priority=Priority.LOW,
        rationale="The abstract says enough.",
        criteria_matched=tuple(c for c, v in verdicts.items() if v in (YES, PARTLY)),
        exclusion_reasons=(),
        model_id="llama3.1:8b",
        prompt_version="screen-3",
        criteria_version=1,
        evidence_kind=TextKind.ABSTRACT,
        decided_at=NOW,
        criterion_answers=tuple(CriterionAnswer(c, v) for c, v in verdicts.items()),
    )


def tier(decision: ScreeningDecision, rules: LabelRules = DEFAULT) -> ScreeningTier | None:
    ruled = label_under(rules, decision, KINDS)
    return None if ruled is None else ruled.tier


class TestLabelRules:
    def test_rules_name_how_many_criteria_possibly_relevant_needs_and_what_unclear_counts_as(
        self,
    ):
        rules = LabelRules(possibly_min_met=2, unclear_counts_as=UnclearCountsAs.MET)

        assert (rules.possibly_min_met, rules.unclear_counts_as) == (2, UnclearCountsAs.MET)

    @pytest.mark.parametrize("value", [0, -1])
    def test_possibly_relevant_needs_at_least_one_criterion_met(self, value):
        with pytest.raises(ValueError, match=r"LabelRules.possibly_min_met"):
            LabelRules(possibly_min_met=value, unclear_counts_as=UnclearCountsAs.MET)

    def test_a_boolean_is_not_a_count(self):
        with pytest.raises(ValueError, match=r"LabelRules.possibly_min_met"):
            LabelRules(possibly_min_met=True, unclear_counts_as=UnclearCountsAs.MET)

    def test_unclear_counts_as_one_of_the_three_choices(self):
        with pytest.raises(ValueError, match=r"LabelRules.unclear_counts_as"):
            LabelRules(possibly_min_met=1, unclear_counts_as="maybe")  # type: ignore[arg-type]


class TestLabelUnder:
    def test_every_inclusion_criterion_yes_is_highly_relevant(self):
        assert tier(decided(YES, YES, YES)) is ScreeningTier.HIGHLY_RELEVANT

    def test_every_inclusion_criterion_met_with_one_partly_is_relevant(self):
        assert tier(decided(YES, PARTLY, YES)) is ScreeningTier.RELEVANT

    def test_enough_inclusion_criteria_met_is_possibly_relevant(self):
        assert tier(decided(YES, NO, NO)) is ScreeningTier.POSSIBLY_RELEVANT

    def test_too_few_inclusion_criteria_met_is_excluded_for_the_criteria_missed(self):
        rules = replace(DEFAULT, possibly_min_met=2)

        ruled = label_under(rules, decided(YES, NO, NO), KINDS)

        assert ruled is not None
        assert (ruled.tier, ruled.exclusion_reasons) == (ScreeningTier.EXCLUDED, ("I2", "I3"))

    def test_an_exclusion_criterion_answered_yes_excludes_whatever_else_is_met(self):
        ruled = label_under(DEFAULT, decided(YES, YES, YES, e1=YES), KINDS)

        assert ruled is not None
        assert (ruled.tier, ruled.exclusion_reasons) == (ScreeningTier.EXCLUDED, ("E1",))

    def test_an_exclusion_criterion_only_partly_met_does_not_exclude(self):
        assert tier(decided(YES, YES, YES, e1=PARTLY)) is ScreeningTier.HIGHLY_RELEVANT

    def test_unclear_counted_as_met_reaches_relevant_but_never_highly_relevant(self):
        rules = replace(DEFAULT, unclear_counts_as=UnclearCountsAs.MET)

        assert tier(decided(YES, UNCLEAR, YES), rules) is ScreeningTier.RELEVANT

    def test_unclear_counted_as_not_met_can_leave_a_paper_possibly_relevant(self):
        assert tier(decided(YES, UNCLEAR, YES)) is ScreeningTier.POSSIBLY_RELEVANT

    def test_unclear_counted_as_uncertain_sends_a_paper_below_relevant_to_a_person(self):
        rules = replace(DEFAULT, unclear_counts_as=UnclearCountsAs.UNCERTAIN)

        assert tier(decided(YES, UNCLEAR, YES), rules) is ScreeningTier.UNCERTAIN
        assert tier(decided(NO, UNCLEAR, NO), rules) is ScreeningTier.UNCERTAIN

    def test_unclear_counted_as_uncertain_leaves_a_paper_that_reaches_relevant_alone(self):
        rules = replace(DEFAULT, unclear_counts_as=UnclearCountsAs.UNCERTAIN)
        unclear_exclusion = decided(YES, YES, YES, e1=UNCLEAR)

        assert tier(unclear_exclusion, rules) is ScreeningTier.HIGHLY_RELEVANT

    def test_a_relabelled_exclusion_is_a_valid_decision(self):
        ruled = label_under(replace(DEFAULT, possibly_min_met=3), decided(YES, NO, NO), KINDS)
        assert ruled is not None

        rebuilt = replace(
            decided(YES, NO, NO),
            label=ruled.label,
            priority=ruled.priority,
            exclusion_reasons=ruled.exclusion_reasons,
        )

        assert rebuilt.tier is ScreeningTier.EXCLUDED

    def test_a_decision_without_answers_is_out_of_the_rules_reach(self):
        bare = replace(decided(YES, YES, YES), criterion_answers=(), criteria_matched=())

        assert label_under(DEFAULT, bare, KINDS) is None

    def test_an_unreadable_decision_is_out_of_the_rules_reach(self):
        unreadable = ScreeningDecision.unreadable(
            run_id=uuid.uuid4(),
            paper_id=uuid.uuid4(),
            model_id="llama3.1:8b",
            prompt_version="screen-3",
            criteria_version=1,
            evidence_kind=TextKind.ABSTRACT,
            decided_at=NOW,
        )

        assert label_under(DEFAULT, unreadable, KINDS) is None

    def test_answers_that_do_not_match_the_criteria_are_out_of_the_rules_reach(self):
        other_plan = {"I1": CriterionKind.INCLUSION, "E1": CriterionKind.EXCLUSION}

        assert label_under(DEFAULT, decided(YES, YES, YES), other_plan) is None


_VERDICTS = st.sampled_from(list(CriterionVerdict))
_RULES = st.builds(
    LabelRules,
    possibly_min_met=st.integers(min_value=1, max_value=5),
    unclear_counts_as=st.sampled_from(list(UnclearCountsAs)),
)


class TestLabelUnderInvariants:
    @given(_RULES, _VERDICTS, _VERDICTS, _VERDICTS, _VERDICTS)
    def test_the_same_answers_and_rules_always_give_the_same_label(self, rules, a, b, c, e):
        first, second = decided(a, b, c, e), decided(a, b, c, e)

        assert label_under(rules, first, KINDS) == label_under(rules, second, KINDS)

    @given(_RULES, _VERDICTS, _VERDICTS, _VERDICTS, _VERDICTS)
    def test_a_paper_with_answers_always_gets_a_label(self, rules, a, b, c, e):
        assert label_under(rules, decided(a, b, c, e), KINDS) is not None

    @given(_RULES, _VERDICTS, _VERDICTS, _VERDICTS)
    def test_rules_never_reach_a_paper_without_answers(self, rules, a, b, c):
        bare = replace(decided(a, b, c), criterion_answers=())

        assert label_under(rules, bare, KINDS) is None

    @given(_RULES, _VERDICTS, _VERDICTS, _VERDICTS, _VERDICTS)
    def test_an_excluded_paper_always_names_a_reason_and_no_other_does(self, rules, a, b, c, e):
        ruled = label_under(rules, decided(a, b, c, e), KINDS)
        assert ruled is not None

        assert bool(ruled.exclusion_reasons) == (ruled.label is ScreeningLabel.EXCLUDE)

    @given(_VERDICTS, _VERDICTS, _VERDICTS, _VERDICTS)
    def test_uncertain_appears_only_when_unclear_is_counted_as_uncertain(self, a, b, c, e):
        for choice in (UnclearCountsAs.MET, UnclearCountsAs.NOT_MET):
            rules = LabelRules(possibly_min_met=1, unclear_counts_as=choice)

            assert tier(decided(a, b, c, e), rules) is not ScreeningTier.UNCERTAIN


class TestMoves:
    def test_moves_count_papers_by_where_they_were_and_where_they_go(self):
        moves = count_moves(
            [
                (ScreeningTier.RELEVANT, ScreeningTier.HIGHLY_RELEVANT),
                (ScreeningTier.RELEVANT, ScreeningTier.HIGHLY_RELEVANT),
                (ScreeningTier.EXCLUDED, ScreeningTier.POSSIBLY_RELEVANT),
                (ScreeningTier.EXCLUDED, ScreeningTier.EXCLUDED),
            ]
        )

        assert moves == (
            TierMove(ScreeningTier.RELEVANT, ScreeningTier.HIGHLY_RELEVANT, 2),
            TierMove(ScreeningTier.EXCLUDED, ScreeningTier.POSSIBLY_RELEVANT, 1),
        )

    def test_a_paper_that_keeps_its_tier_has_not_moved(self):
        assert count_moves([(ScreeningTier.RELEVANT, ScreeningTier.RELEVANT)]) == ()


class TestLabelRulesVersion:
    def version(self, **changes: object) -> LabelRulesVersion:
        values: dict[str, object] = {
            "review_id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "version": 1,
            "rules": DEFAULT,
            "created_by": uuid.uuid4(),
            "created_at": NOW,
            "papers_moved": 4,
        }
        return LabelRulesVersion(**{**values, **changes})  # type: ignore[arg-type]

    def test_a_version_records_the_rules_who_set_them_when_and_how_many_papers_moved(self):
        version = self.version()

        assert (version.version, version.rules, version.papers_moved) == (1, DEFAULT, 4)

    def test_versions_count_from_one(self):
        with pytest.raises(ValueError, match=r"LabelRulesVersion.version"):
            self.version(version=0)

    def test_moved_papers_are_never_negative(self):
        with pytest.raises(ValueError, match=r"LabelRulesVersion.papers_moved"):
            self.version(papers_moved=-1)

    def test_the_time_names_its_zone(self):
        with pytest.raises(ValueError, match=r"LabelRulesVersion.created_at"):
            self.version(created_at=datetime(2026, 11, 12, 9, 0))  # noqa: DTZ001 — the naive value under test
