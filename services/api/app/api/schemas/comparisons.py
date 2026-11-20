"""Response models for brief versions and for comparing versions or runs.

Responses only. Each comparison reads from `before` (the older) to `after` (the newer),
whichever order the ids came in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.comparisons import ComparedBriefs
from app.domain.briefs import BriefRecord, EvidenceStrength, SourceKind
from app.domain.comparisons import (
    BriefComparison,
    BriefSource,
    Change,
    ComparedRun,
    DiscoveryCounts,
    RunComparison,
    ScreenedPaper,
)
from app.domain.screening import ScreeningLabel


class BriefVersionOut(BaseModel):
    id: UUID
    version: int
    run_id: UUID = Field(description="The run whose Brief stage wrote this version.")
    created_at: datetime
    model_id: str
    is_latest: bool

    @classmethod
    def from_domain(cls, record: BriefRecord, latest: int) -> BriefVersionOut:
        return cls(
            id=record.id,
            version=record.version,
            run_id=record.run_id,
            created_at=record.created_at,
            model_id=record.model_id,
            is_latest=record.version == latest,
        )


class StrengthChangeOut(BaseModel):
    option: str = Field(description="As the newer version names it.")
    before: EvidenceStrength
    after: EvidenceStrength


class BriefSourceOut(BaseModel):
    paper_id: UUID
    source_kind: SourceKind
    tag: str = Field(description="Its tag in the version that cites it.")
    title: str | None

    @classmethod
    def from_domain(cls, source: BriefSource) -> BriefSourceOut:
        return cls(
            paper_id=source.paper_id,
            source_kind=source.source_kind,
            tag=source.tag,
            title=source.title,
        )


class BriefChangesOut(BaseModel):
    options_added: list[str]
    options_removed: list[str]
    strength_changes: list[StrengthChangeOut]
    sources_added: list[BriefSourceOut] = Field(description="Matched by paper, not by tag.")
    sources_removed: list[BriefSourceOut]

    @classmethod
    def changes_of(cls, changes: BriefComparison) -> dict[str, Any]:
        return {
            "options_added": list(changes.options_added),
            "options_removed": list(changes.options_removed),
            "strength_changes": [
                StrengthChangeOut(option=c.option, before=c.before, after=c.after)
                for c in changes.strength_changes
            ],
            "sources_added": [BriefSourceOut.from_domain(s) for s in changes.sources_added],
            "sources_removed": [BriefSourceOut.from_domain(s) for s in changes.sources_removed],
        }

    @classmethod
    def from_domain(cls, changes: BriefComparison) -> BriefChangesOut:
        return cls(**cls.changes_of(changes))


class ComparedVersionOut(BaseModel):
    id: UUID
    version: int
    run_id: UUID
    created_at: datetime


class BriefComparisonOut(BriefChangesOut):
    before: ComparedVersionOut
    after: ComparedVersionOut

    @classmethod
    def from_compared(cls, compared: ComparedBriefs) -> BriefComparisonOut:
        return cls(
            before=_version(compared.before),
            after=_version(compared.after),
            **cls.changes_of(compared.changes),
        )


def _version(record: BriefRecord) -> ComparedVersionOut:
    return ComparedVersionOut(
        id=record.id, version=record.version, run_id=record.run_id, created_at=record.created_at
    )


class BeforeAfterOut(BaseModel):
    before: Any = Field(description="As the older run recorded it; null if it recorded none.")
    after: Any

    @classmethod
    def from_domain(cls, change: Change) -> BeforeAfterOut:
        return cls(before=change.before, after=change.after)


class NamedChangeOut(BeforeAfterOut):
    name: str = Field(
        description="`stage.fact` for a stage's own setting, e.g. `discover.limit_per_source`."
    )

    @classmethod
    def named(cls, change: Change) -> NamedChangeOut:
        return cls(name=change.name, before=change.before, after=change.after)


class SourceQueryOut(BaseModel):
    source: str
    query: str


class SetupOut(BaseModel):
    vocabulary_versions: BeforeAfterOut
    sources: BeforeAfterOut
    model_ids: BeforeAfterOut
    queries_added: list[SourceQueryOut]
    queries_removed: list[SourceQueryOut] = Field(description="A rewritten query is in both.")
    prompt_versions: list[NamedChangeOut] = Field(description="Every prompt either run used.")
    settings_changed: list[NamedChangeOut] = Field(description="Only those that differ.")


class ComparedPaperOut(BaseModel):
    paper_id: UUID
    title: str

    @classmethod
    def from_domain(cls, paper: ScreenedPaper) -> ComparedPaperOut:
        return cls(paper_id=paper.paper_id, title=paper.title)


class DiscoveryCountsOut(BaseModel):
    identified: int = Field(description="Records the sources returned.")
    after_deduplication: int

    @classmethod
    def from_domain(cls, counts: DiscoveryCounts) -> DiscoveryCountsOut:
        return cls(identified=counts.identified, after_deduplication=counts.after_deduplication)


class DiscoveryOut(BaseModel):
    before: DiscoveryCountsOut
    after: DiscoveryCountsOut
    only_before: list[ComparedPaperOut]
    only_after: list[ComparedPaperOut]
    in_both: int


class LabelChangeOut(BaseModel):
    paper_id: UUID
    title: str
    before: ScreeningLabel
    after: ScreeningLabel
    reason: str | None = Field(description="Why the newer run labelled it so.")


class ScreeningOut(BaseModel):
    before: dict[str, int] = Field(description="include, exclude, uncertain and unscreened.")
    after: dict[str, int]
    changed: list[LabelChangeOut]


class ComparedRunOut(BaseModel):
    run_id: UUID
    number: int = Field(description="Its place among the review's runs, from 1, oldest first.")
    created_at: datetime
    has_record: bool = Field(description="False when no stage recorded its setup.")

    @classmethod
    def from_domain(cls, run: ComparedRun) -> ComparedRunOut:
        return cls(
            run_id=run.run_id,
            number=run.number,
            created_at=run.created_at,
            has_record=run.has_record,
        )


class RunComparisonOut(BaseModel):
    before: ComparedRunOut
    after: ComparedRunOut
    cannot_compare: str | None = Field(
        description="Why the runs cannot be compared; null when they can."
    )
    setup: SetupOut | None
    discovery: DiscoveryOut | None
    screening: ScreeningOut | None
    brief: BriefChangesOut | None = Field(description="Null unless both runs wrote a brief.")

    @classmethod
    def from_domain(cls, compared: RunComparison) -> RunComparisonOut:
        setup, discovery, screening = compared.setup, compared.discovery, compared.screening
        return cls(
            before=ComparedRunOut.from_domain(compared.before),
            after=ComparedRunOut.from_domain(compared.after),
            cannot_compare=_cannot_compare(compared),
            setup=None
            if setup is None
            else SetupOut(
                vocabulary_versions=BeforeAfterOut.from_domain(setup.vocabulary_versions),
                sources=BeforeAfterOut.from_domain(setup.sources),
                model_ids=BeforeAfterOut.from_domain(setup.model_ids),
                queries_added=[
                    SourceQueryOut(source=q.source, query=q.query) for q in setup.queries_added
                ],
                queries_removed=[
                    SourceQueryOut(source=q.source, query=q.query)
                    for q in setup.queries_removed
                ],
                prompt_versions=[NamedChangeOut.named(c) for c in setup.prompt_versions],
                settings_changed=[NamedChangeOut.named(c) for c in setup.settings_changed],
            ),
            discovery=None
            if discovery is None
            else DiscoveryOut(
                before=DiscoveryCountsOut.from_domain(discovery.before),
                after=DiscoveryCountsOut.from_domain(discovery.after),
                only_before=[ComparedPaperOut.from_domain(p) for p in discovery.only_before],
                only_after=[ComparedPaperOut.from_domain(p) for p in discovery.only_after],
                in_both=discovery.in_both,
            ),
            screening=None
            if screening is None
            else ScreeningOut(
                before=dict(screening.before),
                after=dict(screening.after),
                changed=[
                    LabelChangeOut(
                        paper_id=c.paper_id,
                        title=c.title,
                        before=c.before,
                        after=c.after,
                        reason=c.reason,
                    )
                    for c in screening.changed
                ],
            ),
            brief=None
            if compared.brief is None
            else BriefChangesOut.from_domain(compared.brief),
        )


def _cannot_compare(compared: RunComparison) -> str | None:
    missing = [run.number for run in (compared.before, compared.after) if not run.has_record]
    if not missing:
        return None
    if len(missing) == 1:
        return f"Run {missing[0]} can't be compared: it has no record of its setup."
    first, second = missing
    return f"Runs {first} and {second} can't be compared: neither has a record of its setup."
