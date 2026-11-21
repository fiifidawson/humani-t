"""The coverage estimate: roughly what share of the relevant papers a run's searches found.

Capture-recapture across paper sources (ADR 0021): how often two sources found the same
relevant papers says how many both missed. Pure arithmetic on counts; which papers count
as relevant, and which sources were searched, is the caller's to say.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from app.domain.runs import JsonValue

COVERAGE_METHOD = "chapman-pairs"
COVERAGE_METHOD_VERSION = 1
# Robson and Regier's rule of thumb: below 7 recaptures, Chapman's estimate is too biased.
MIN_OVERLAP = 7
_Z_95 = 1.96
_ROUND_TO = 5


class NoEstimateReason(StrEnum):
    SCREENING_UNFINISHED = "screening_unfinished"
    ONE_SOURCE = "one_source"
    TOO_FEW_OVERLAPS = "too_few_overlaps"
    SOURCES_NESTED = "sources_nested"


@dataclass(frozen=True, slots=True)
class SourceOverlap:
    """Relevant papers one pair of sources found: each one's, and those both found."""

    sources: tuple[str, str]
    first_found: int
    second_found: int
    both_found: int

    def __post_init__(self) -> None:
        if min(self.first_found, self.second_found, self.both_found) < 0:
            raise ValueError("SourceOverlap counts must not be negative")
        if self.both_found > min(self.first_found, self.second_found):
            raise ValueError("SourceOverlap.both_found cannot exceed what either source found")

    @property
    def is_nested(self) -> bool:
        """Whether one source found nothing relevant the other did not."""
        return self.both_found in (self.first_found, self.second_found)


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    """A 95% range for the share found, in whole fives, and the pair it came from."""

    sources: tuple[str, str]
    estimated_total: int
    low_percent: int
    high_percent: int

    def __post_init__(self) -> None:
        if not 0 <= self.low_percent <= self.high_percent <= 100:
            raise ValueError(
                "CoverageInterval.low_percent must be at most high_percent, both 0 to 100"
            )
        if self.estimated_total < 0:
            raise ValueError("CoverageInterval.estimated_total must not be negative")


@dataclass(frozen=True, slots=True)
class CoverageEstimate:
    """A range, or no estimate and why: never both. `overlaps` are the counts it used."""

    relevant_found: int
    searched_sources: tuple[str, ...]
    overlaps: tuple[SourceOverlap, ...]
    interval: CoverageInterval | None
    no_estimate_because: NoEstimateReason | None
    method: str = COVERAGE_METHOD
    method_version: int = COVERAGE_METHOD_VERSION

    def __post_init__(self) -> None:
        if (self.interval is None) == (self.no_estimate_because is None):
            raise ValueError(
                "CoverageEstimate needs exactly one of interval and no_estimate_because"
            )
        if self.relevant_found < 0:
            raise ValueError("CoverageEstimate.relevant_found must not be negative")


def estimate_coverage(
    *,
    searched_sources: Sequence[str],
    relevant_found_by: Sequence[Sequence[str]],
    is_screening_finished: bool = True,
) -> CoverageEstimate:
    """The share of relevant papers found, from which sources found each relevant one.

    `searched_sources` are those that answered; one that failed found nothing and is left
    out. `relevant_found_by` has one entry per relevant paper. Sources are taken in name
    order, so the order they were listed in never changes the result.
    """
    sources = tuple(sorted(set(searched_sources)))
    for found_by in relevant_found_by:
        unknown = set(found_by) - set(sources)
        if unknown:
            raise ValueError(f"a relevant paper names sources not searched: {sorted(unknown)}")
    overlaps = tuple(
        SourceOverlap(
            sources=(a, b),
            first_found=sum(a in f for f in relevant_found_by),
            second_found=sum(b in f for f in relevant_found_by),
            both_found=sum(a in f and b in f for f in relevant_found_by),
        )
        for a, b in combinations(sources, 2)
    )
    relevant = len(relevant_found_by)

    def none(reason: NoEstimateReason) -> CoverageEstimate:
        return CoverageEstimate(relevant, sources, overlaps, None, reason)

    if not is_screening_finished:
        return none(NoEstimateReason.SCREENING_UNFINISHED)
    if len(sources) < 2:
        return none(NoEstimateReason.ONE_SOURCE)
    enough = [o for o in overlaps if o.both_found >= MIN_OVERLAP]
    usable = [o for o in enough if not o.is_nested]
    if not usable:
        return none(
            NoEstimateReason.SOURCES_NESTED if enough else NoEstimateReason.TOO_FEW_OVERLAPS
        )
    # Sources that index the same journals overlap more than chance, which makes every
    # pair optimistic; the pair that suggests the most papers missed is the least so.
    cautious = max(usable, key=_chapman_total)
    return CoverageEstimate(relevant, sources, overlaps, _interval(cautious, relevant), None)


def _chapman_total(overlap: SourceOverlap) -> float:
    n1, n2, m = overlap.first_found, overlap.second_found, overlap.both_found
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def _interval(overlap: SourceOverlap, relevant: int) -> CoverageInterval:
    """Chao's (1987) log-normal 95% range, which never goes below what the pair found."""
    n1, n2, m = overlap.first_found, overlap.second_found, overlap.both_found
    seen = n1 + n2 - m
    unseen = (n1 - m) * (n2 - m) / (m + 1)
    variance = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    spread = math.exp(_Z_95 * math.sqrt(math.log(1 + variance / unseen**2)))
    # A third source's own finds are found papers too, so no bound is below them.
    total = max(_chapman_total(overlap), relevant)
    lowest = max(seen + unseen / spread, relevant)
    highest = max(seen + unseen * spread, relevant)
    return CoverageInterval(
        sources=overlap.sources,
        estimated_total=round(total),
        low_percent=_rounded_down(100 * relevant / highest),
        high_percent=min(100, _rounded_up(100 * relevant / lowest)),
    )


def _rounded_down(percent: float) -> int:
    return math.floor(percent / _ROUND_TO) * _ROUND_TO


def _rounded_up(percent: float) -> int:
    return math.ceil(percent / _ROUND_TO) * _ROUND_TO


def coverage_facts(estimate: CoverageEstimate) -> dict[str, JsonValue]:
    """The estimate as a run's manifest records it: method, version, counts and result."""
    interval = estimate.interval
    return {
        "method": estimate.method,
        "method_version": estimate.method_version,
        "relevant_found": estimate.relevant_found,
        "searched_sources": list(estimate.searched_sources),
        "overlaps": [
            {
                "sources": list(o.sources),
                "first_found": o.first_found,
                "second_found": o.second_found,
                "both_found": o.both_found,
            }
            for o in estimate.overlaps
        ],
        "interval": None
        if interval is None
        else {
            "sources": list(interval.sources),
            "estimated_total": interval.estimated_total,
            "low_percent": interval.low_percent,
            "high_percent": interval.high_percent,
        },
        "no_estimate_because": None
        if estimate.no_estimate_because is None
        else estimate.no_estimate_because.value,
    }
