"""Comparing two versions of a brief, or two runs of one review, as rules with no I/O.

Every comparison reads from the older side (`before`) to the newer (`after`) and says only
what differs. Nothing here judges which side is better, and nothing is reconstructed: a
run's setup comes from its `RunManifest`, as its stages recorded it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.briefs import BriefBody, EvidenceStrength, SourceKind
from app.domain.manifest import RunManifest
from app.domain.runs import JsonValue
from app.domain.screening import ScreeningLabel

UNSCREENED = "unscreened"

# Facts the named parts of a setup comparison already show, so not repeated as settings.
_SHOWN_ELSEWHERE = frozenset(
    {
        "vocabulary_version",
        "criteria_version",
        "plan_version",
        "instructions_version",
        "sources",
        "queries",
        "model_id",
        "model_ids",
        "prompt_version",
        "prompt_versions",
        "is_masked",
        "papers_from_run",
    }
)


@dataclass(frozen=True, slots=True)
class BriefSource:
    """A paper or team document a brief cites, by id, with the tag it had in that brief."""

    paper_id: UUID
    source_kind: SourceKind
    tag: str
    title: str | None


@dataclass(frozen=True, slots=True)
class StrengthChange:
    option: str
    before: EvidenceStrength
    after: EvidenceStrength


@dataclass(frozen=True, slots=True)
class BriefComparison:
    options_added: tuple[str, ...]
    options_removed: tuple[str, ...]
    strength_changes: tuple[StrengthChange, ...]
    sources_added: tuple[BriefSource, ...]
    sources_removed: tuple[BriefSource, ...]


def _option_key(name: str) -> str:
    return " ".join(name.casefold().split())


def compare_briefs(
    before: BriefBody,
    after: BriefBody,
    *,
    sources_before: Sequence[BriefSource],
    sources_after: Sequence[BriefSource],
) -> BriefComparison:
    """What changed from one brief to another: options, their strength, and cited sources.

    Options match by name, ignoring case and spacing. Sources match by paper, never by
    tag, since a tag is only a paper's place in one run's list.
    """
    # A renamed option ("Cash" to "Cash transfers") shows as one removed and one added:
    # guessing that two names mean the same option would hide a real change.
    old = {_option_key(o.name): o for o in before.options}
    new = {_option_key(o.name): o for o in after.options}
    old_papers = {s.paper_id for s in sources_before}
    new_papers = {s.paper_id for s in sources_after}
    return BriefComparison(
        options_added=tuple(o.name for k, o in new.items() if k not in old),
        options_removed=tuple(o.name for k, o in old.items() if k not in new),
        strength_changes=tuple(
            StrengthChange(o.name, old[k].strength, o.strength)
            for k, o in new.items()
            if k in old and old[k].strength is not o.strength
        ),
        sources_added=tuple(s for s in sources_after if s.paper_id not in old_papers),
        sources_removed=tuple(s for s in sources_before if s.paper_id not in new_papers),
    )


@dataclass(frozen=True, slots=True)
class Change:
    """One named value in both runs; `None` where a run recorded none."""

    name: str
    before: JsonValue
    after: JsonValue

    @property
    def is_changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class SourceQuery:
    source: str
    query: str


@dataclass(frozen=True, slots=True)
class SetupComparison:
    vocabulary_versions: Change
    sources: Change
    model_ids: Change
    queries_added: tuple[SourceQuery, ...]
    queries_removed: tuple[SourceQuery, ...]
    prompt_versions: tuple[Change, ...]
    settings_changed: tuple[Change, ...]


def _stage_settings(manifest: RunManifest, stages: frozenset[str]) -> dict[str, JsonValue]:
    return {
        f"{stage.stage}.{key}": value
        for stage in manifest.stage_facts
        if stage.stage in stages
        for key, value in stage.facts.items()
        if key not in _SHOWN_ELSEWHERE and not isinstance(value, dict)
    }


def compare_setup(before: RunManifest, after: RunManifest) -> SetupComparison:
    """Both runs' setups side by side, and the settings that changed between them.

    A stage's own settings are compared only when both runs finished it: a run that
    redid only the brief did not change how the other's papers were found.
    """
    old_queries, new_queries = _queries(before), _queries(after)
    prompts = dict.fromkeys([*before.prompt_versions, *after.prompt_versions])
    named = (
        Change(
            "criteria_version", list(before.criteria_versions), list(after.criteria_versions)
        ),
        Change("plan_version", list(before.plan_versions), list(after.plan_versions)),
        Change(
            "instructions_version",
            list(before.instructions_versions),
            list(after.instructions_versions),
        ),
        Change("masking", before.masking, after.masking),
    )
    shared = frozenset(s.stage for s in before.stage_facts) & frozenset(
        s.stage for s in after.stage_facts
    )
    older, newer = _stage_settings(before, shared), _stage_settings(after, shared)
    by_stage = tuple(
        Change(name, older.get(name), newer.get(name)) for name in {**older, **newer}
    )
    return SetupComparison(
        vocabulary_versions=Change(
            "vocabulary_version",
            list(before.vocabulary_versions),
            list(after.vocabulary_versions),
        ),
        sources=Change("sources", list(before.sources), list(after.sources)),
        model_ids=Change("model_ids", list(before.model_ids), list(after.model_ids)),
        queries_added=tuple(q for q in new_queries if q not in old_queries),
        queries_removed=tuple(q for q in old_queries if q not in new_queries),
        prompt_versions=tuple(
            Change(name, before.prompt_versions.get(name), after.prompt_versions.get(name))
            for name in prompts
        ),
        settings_changed=tuple(c for c in (*named, *by_stage) if c.is_changed),
    )


def _queries(manifest: RunManifest) -> list[SourceQuery]:
    return [SourceQuery(s, q) for s, q in manifest.queries.items()]


@dataclass(frozen=True, slots=True)
class ScreenedPaper:
    """A paper in one run's list, with the label that run's screening gave it, if any."""

    paper_id: UUID
    title: str
    label: ScreeningLabel | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RunPapers:
    """What one run found: how many records its sources returned, and its merged list."""

    identified: int
    papers: tuple[ScreenedPaper, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryCounts:
    identified: int
    after_deduplication: int


@dataclass(frozen=True, slots=True)
class DiscoveryComparison:
    before: DiscoveryCounts
    after: DiscoveryCounts
    only_before: tuple[ScreenedPaper, ...]
    only_after: tuple[ScreenedPaper, ...]
    in_both: int


def compare_discovery(before: RunPapers, after: RunPapers) -> DiscoveryComparison:
    old = {p.paper_id for p in before.papers}
    new = {p.paper_id for p in after.papers}
    return DiscoveryComparison(
        before=DiscoveryCounts(before.identified, len(before.papers)),
        after=DiscoveryCounts(after.identified, len(after.papers)),
        only_before=tuple(p for p in before.papers if p.paper_id not in new),
        only_after=tuple(p for p in after.papers if p.paper_id not in old),
        in_both=len(old & new),
    )


@dataclass(frozen=True, slots=True)
class LabelChange:
    """A paper both runs labelled, differently; `reason` is why the newer run labelled it."""

    paper_id: UUID
    title: str
    before: ScreeningLabel
    after: ScreeningLabel
    reason: str | None


@dataclass(frozen=True, slots=True)
class ScreeningComparison:
    before: Mapping[str, int]
    after: Mapping[str, int]
    changed: tuple[LabelChange, ...]


def _label_counts(papers: RunPapers) -> dict[str, int]:
    counts = {label.value: 0 for label in ScreeningLabel}
    counts[UNSCREENED] = 0
    for paper in papers.papers:
        counts[UNSCREENED if paper.label is None else paper.label.value] += 1
    return counts


def compare_screening(before: RunPapers, after: RunPapers) -> ScreeningComparison:
    old = {p.paper_id: p.label for p in before.papers if p.label is not None}
    return ScreeningComparison(
        before=_label_counts(before),
        after=_label_counts(after),
        changed=tuple(
            LabelChange(p.paper_id, p.title, old[p.paper_id], p.label, p.reason)
            for p in after.papers
            if p.label is not None and p.paper_id in old and old[p.paper_id] is not p.label
        ),
    )


@dataclass(frozen=True, slots=True)
class ComparedRun:
    """One side of a run comparison. `number` counts the review's runs from 1, oldest first."""

    run_id: UUID
    number: int
    created_at: datetime
    has_record: bool


@dataclass(frozen=True, slots=True)
class RunComparison:
    """Two runs of one review, part by part; every part is `None` unless both have a record.

    `brief` is also `None` when either run wrote no brief.
    """

    before: ComparedRun
    after: ComparedRun
    setup: SetupComparison | None = None
    discovery: DiscoveryComparison | None = None
    screening: ScreeningComparison | None = None
    brief: BriefComparison | None = None

    @property
    def is_comparable(self) -> bool:
        return self.before.has_record and self.after.has_record
