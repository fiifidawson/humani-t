"""Requirements for the coverage estimate (UI_UX_SPEC §6.8, ADR 0021).

Capture-recapture across paper sources: how many of the relevant papers one source found
another found too says roughly how many neither found. When the data cannot support an
honest range, there is no estimate and a reason, never a made-up number.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domain.coverage import (
    COVERAGE_METHOD,
    COVERAGE_METHOD_VERSION,
    MIN_OVERLAP,
    CoverageEstimate,
    CoverageInterval,
    NoEstimateReason,
    SourceOverlap,
    coverage_facts,
    estimate_coverage,
)


def found(both: int, only_first: int, only_second: int, a: str = "a", b: str = "b"):
    """Relevant papers' sources: `both` found by a and b, then each one's own."""
    return [(a, b)] * both + [(a,)] * only_first + [(b,)] * only_second


def estimate(
    relevant: Sequence[Sequence[str]], sources: Sequence[str] = ("a", "b")
) -> CoverageEstimate:
    return estimate_coverage(searched_sources=sources, relevant_found_by=relevant)


class TestAnEstimate:
    def test_two_sources_that_share_enough_relevant_papers_give_a_range(self):
        result = estimate(found(8, 2, 2))

        assert result.no_estimate_because is None
        assert result.interval is not None
        assert (result.interval.low_percent, result.interval.high_percent) == (70, 100)
        assert result.relevant_found == 12

    def test_the_estimate_names_its_method_and_version(self):
        result = estimate(found(8, 2, 2))

        assert (result.method, result.method_version) == (
            COVERAGE_METHOD,
            COVERAGE_METHOD_VERSION,
        )
        assert COVERAGE_METHOD == "chapman-pairs"

    def test_the_estimated_total_is_the_chapman_estimate_rounded(self):
        result = estimate(found(8, 2, 2))

        # (10 + 1)(10 + 1) / (8 + 1) - 1 = 12.44
        assert result.interval is not None
        assert result.interval.estimated_total == 12

    def test_the_range_is_rounded_outward_to_whole_fives(self):
        result = estimate(found(8, 12, 12))

        assert result.interval is not None
        low, high = result.interval.low_percent, result.interval.high_percent
        # 32 found of an estimated 48, between 37.8 and 76.1: 42.1% to 84.6%.
        assert (low, high) == (40, 85)
        assert result.interval.estimated_total == 48

    def test_coverage_never_exceeds_100_percent(self):
        result = estimate(found(40, 1, 1))

        assert result.interval is not None
        assert result.interval.high_percent == 100

    def test_every_pair_of_sources_keeps_its_counts(self):
        result = estimate(found(8, 2, 3))

        assert result.overlaps == (
            SourceOverlap(sources=("a", "b"), first_found=10, second_found=11, both_found=8),
        )

    def test_the_interval_names_the_pair_it_came_from(self):
        result = estimate(found(8, 2, 2))

        assert result.interval is not None
        assert result.interval.sources == ("a", "b")


class TestThreeSources:
    def test_the_most_cautious_pair_sets_the_range(self):
        # a and b agree closely; a and c less so, which means more papers missed.
        relevant = [
            *[("a", "b", "c")] * 7,
            *[("a", "b")] * 3,
            ("a", "c"),
            *[("a",)] * 2,
            ("b",),
            *[("c",)] * 4,
        ]

        result = estimate(relevant, ("a", "b", "c"))

        assert result.interval is not None
        assert result.interval.sources == ("a", "c")
        assert len(result.overlaps) == 3

    def test_papers_only_a_third_source_found_still_count_as_found(self):
        relevant = [*found(8, 2, 2), ("c",), ("c",)]

        result = estimate(relevant, ("a", "b", "c"))

        assert result.relevant_found == 14
        assert result.interval is not None
        assert result.interval.estimated_total >= 14

    def test_a_pair_that_shares_too_little_is_left_out_rather_than_trusted(self):
        relevant = [*found(8, 2, 2), ("a", "c")]

        result = estimate(relevant, ("a", "b", "c"))

        assert result.interval is not None
        assert result.interval.sources == ("a", "b")


class TestNoEstimate:
    def test_one_source_gives_no_estimate(self):
        result = estimate([("a",)] * 20, ("a",))

        assert result.interval is None
        assert result.no_estimate_because is NoEstimateReason.ONE_SOURCE

    def test_fewer_shared_papers_than_the_minimum_give_no_estimate(self):
        result = estimate(found(MIN_OVERLAP - 1, 4, 4))

        assert result.interval is None
        assert result.no_estimate_because is NoEstimateReason.TOO_FEW_OVERLAPS

    def test_the_minimum_is_seven_shared_papers(self):
        assert MIN_OVERLAP == 7
        assert estimate(found(7, 2, 2)).interval is not None

    def test_no_relevant_papers_give_no_estimate(self):
        result = estimate([])

        assert result.no_estimate_because is NoEstimateReason.TOO_FEW_OVERLAPS
        assert result.relevant_found == 0

    def test_a_source_whose_finds_the_other_all_found_gives_no_estimate(self):
        # Every relevant paper b found, a found too: shared records, not proof of none missed.
        result = estimate(found(9, 6, 0))

        assert result.interval is None
        assert result.no_estimate_because is NoEstimateReason.SOURCES_NESTED

    def test_unfinished_screening_gives_no_estimate(self):
        result = estimate_coverage(
            searched_sources=("a", "b"),
            relevant_found_by=found(8, 2, 2),
            is_screening_finished=False,
        )

        assert result.interval is None
        assert result.no_estimate_because is NoEstimateReason.SCREENING_UNFINISHED

    def test_a_paper_naming_a_source_that_was_not_searched_is_refused(self):
        with pytest.raises(ValueError, match="not searched"):
            estimate([("a", "z")])


class TestValidity:
    def test_an_estimate_with_both_a_range_and_a_reason_is_refused(self):
        with pytest.raises(ValueError, match="exactly one"):
            CoverageEstimate(
                relevant_found=1,
                searched_sources=("a", "b"),
                overlaps=(),
                interval=CoverageInterval(("a", "b"), 1, 50, 100),
                no_estimate_because=NoEstimateReason.ONE_SOURCE,
            )

    def test_an_overlap_larger_than_either_source_is_refused(self):
        with pytest.raises(ValueError, match="both_found"):
            SourceOverlap(sources=("a", "b"), first_found=3, second_found=9, both_found=4)

    def test_a_range_that_runs_backwards_is_refused(self):
        with pytest.raises(ValueError, match="low_percent"):
            CoverageInterval(("a", "b"), 10, 90, 60)


class TestRecordedFacts:
    def test_the_facts_name_the_method_its_version_and_the_counts_used(self):
        facts = coverage_facts(estimate(found(8, 2, 2)))

        assert facts == {
            "method": "chapman-pairs",
            "method_version": 1,
            "relevant_found": 12,
            "searched_sources": ["a", "b"],
            "overlaps": [
                {"sources": ["a", "b"], "first_found": 10, "second_found": 10, "both_found": 8}
            ],
            "interval": {
                "sources": ["a", "b"],
                "estimated_total": 12,
                "low_percent": 70,
                "high_percent": 100,
            },
            "no_estimate_because": None,
        }

    def test_the_facts_of_no_estimate_carry_the_reason(self):
        facts = coverage_facts(estimate([("a",)] * 3, ("a",)))

        assert facts["interval"] is None
        assert facts["no_estimate_because"] == "one_source"
