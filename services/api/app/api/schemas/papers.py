"""Response models for papers, screening decisions, PRISMA counts and the run manifest.

Responses only: these routes take no body. Each model is built from an
application view, so the wire format never reaches the domain. Paper text is
served masked (`masked_text`); a paper's raw kept text has no field here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.papers import PaperDetail, PaperPage, PaperRow, PrismaView
from app.domain.discovery import TextKind
from app.domain.manifest import RunManifest
from app.domain.screening import (
    CriterionVerdict,
    Priority,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
)

_Status = Literal["screened", "skipped", "failed", "not_screened"]
_SkipReason = Literal["no_abstract", "not_masked"]


class CriterionAnswerOut(BaseModel):
    id: str = Field(description="The criterion (I1, E1…) of plan version `criteria_version`.")
    answer: CriterionVerdict = Field(description="yes and partly count as met.")
    quote: str | None = Field(
        description="Words of the masked text the model read, checked to be there; or null."
    )


class DecisionOut(BaseModel):
    label: ScreeningLabel
    priority: Priority
    tier: ScreeningTier = Field(description="The name shown: Highly relevant, Relevant, …")
    score: float
    rationale: str = Field(description="The model's reason, written from masked text.")
    criteria_matched: list[str] = Field(description="Criterion ids (I1, E1…) the paper meets.")
    exclusion_reasons: list[str] = Field(
        description="For an exclusion: the criteria met (E…) and missed (I…)."
    )
    is_unreadable: bool = Field(description="True when the model's answer could not be read.")
    model_id: str
    prompt_version: str
    criteria_version: int = Field(description="The plan version whose criteria were applied.")
    evidence_kind: TextKind = Field(description="What was read: the abstract.")
    decided_at: datetime
    criteria: list[CriterionAnswerOut] = Field(
        default_factory=list,
        description=(
            "Each criterion's answer and quote, in the plan's order. Empty for decisions "
            "made before prompt version screen-2, or when the model gave none."
        ),
    )

    @classmethod
    def from_domain(cls, decision: ScreeningDecision) -> DecisionOut:
        return cls(
            label=decision.label,
            priority=decision.priority,
            tier=decision.tier,
            score=decision.score,
            rationale=decision.rationale,
            criteria_matched=list(decision.criteria_matched),
            exclusion_reasons=list(decision.exclusion_reasons),
            is_unreadable=decision.is_unreadable,
            model_id=decision.model_id,
            prompt_version=decision.prompt_version,
            criteria_version=decision.criteria_version,
            evidence_kind=decision.evidence_kind,
            decided_at=decision.decided_at,
            criteria=[
                CriterionAnswerOut(id=a.criterion_id, answer=a.verdict, quote=a.quote)
                for a in decision.criterion_answers
            ],
        )


class PaperOut(BaseModel):
    paper_id: UUID
    tag: str = Field(description="How the brief cites it: P and its place in the run.")
    position: int
    title: str
    year: int | None
    doi: str | None
    url: str | None = Field(description="Its own link, else https://doi.org/<doi>, else null.")
    source_id: str = Field(description="The source whose record was kept.")
    found_by: list[str] = Field(description="Every source that returned it in this run.")
    is_open_access: bool
    decision: DecisionOut | None = Field(description="None until it is screened.")
    screening_status: _Status = Field(
        default="screened",
        description=(
            "skipped: no masked abstract to send; failed: the privacy check stopped its "
            "text; not_screened: this run has not screened it."
        ),
    )
    skipped_because: _SkipReason | None = Field(default=None, description="Set when skipped.")

    @classmethod
    def from_row(cls, row: PaperRow) -> PaperOut:
        record = row.record
        return cls(
            paper_id=row.paper_id,
            tag=row.tag,
            position=row.position,
            title=record.title,
            year=record.year,
            doi=record.doi,
            url=record.link,
            source_id=record.source_id,
            found_by=list(row.found_by),
            is_open_access=record.is_open_access,
            decision=None if row.decision is None else DecisionOut.from_domain(row.decision),
            screening_status=cast(_Status, row.status),
            skipped_because=cast(_SkipReason | None, row.skipped_because),
        )


class PaperPageOut(BaseModel):
    run_id: UUID
    page: int
    page_size: int
    total: int = Field(description="Papers matching the filter, on every page.")
    counts: dict[str, int] = Field(
        description=(
            "Papers per tier in the run; unscreened (all without a decision), and of "
            "those: skipped, failed and not_screened."
        )
    )
    papers: list[PaperOut]

    @classmethod
    def from_view(cls, view: PaperPage) -> PaperPageOut:
        return cls(
            run_id=view.run_id,
            page=view.page,
            page_size=view.page_size,
            total=view.total,
            counts=dict(view.counts),
            papers=[PaperOut.from_row(row) for row in view.rows],
        )


class PaperDetailOut(BaseModel):
    run_id: UUID
    paper: PaperOut
    text_kind: TextKind | None = Field(description="What was kept for screening, if anything.")
    masked_text: str | None = Field(description="The kept text, masked; None until masked.")

    @classmethod
    def from_view(cls, view: PaperDetail) -> PaperDetailOut:
        return cls(
            run_id=view.run_id,
            paper=PaperOut.from_row(view.row),
            text_kind=view.text_kind,
            masked_text=view.masked_text,
        )


class PrismaOut(BaseModel):
    run_id: UUID
    identified: int
    identified_by_source: dict[str, int]
    failed_sources: list[str]
    duplicates_removed: int
    after_deduplication: int
    screened: int
    included: int
    excluded: int
    uncertain: int
    not_screened: int = Field(description="Skipped (no abstract, not masked) or failed.")
    excluded_by_reason: dict[str, int] = Field(description="Exclusions per criterion id.")

    @classmethod
    def from_view(cls, view: PrismaView) -> PrismaOut:
        c = view.counts
        return cls(
            run_id=view.run_id,
            identified=c.identified,
            identified_by_source=dict(c.identified_by_source),
            failed_sources=list(c.failed_sources),
            duplicates_removed=c.duplicates_removed,
            after_deduplication=c.after_deduplication,
            screened=c.screened,
            included=c.included,
            excluded=c.excluded,
            uncertain=c.uncertain,
            not_screened=c.not_screened,
            excluded_by_reason=dict(c.excluded_by_reason),
        )


class StageFactsOut(BaseModel):
    stage: str
    seconds: float
    facts: dict[str, Any] = Field(description="What the stage recorded under `manifest`.")


class ManifestOut(BaseModel):
    run_id: UUID
    review_id: UUID
    stages: list[str]
    outcome: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    is_complete: bool = Field(
        description="Whether every requested stage finished and reported."
    )
    vocabulary_versions: list[int]
    criteria_versions: list[int]
    plan_versions: list[int]
    sources: list[str]
    queries: dict[str, str]
    model_ids: list[str]
    prompt_versions: dict[str, str]
    papers_from_runs: list[str] = Field(description="Runs whose discovery this run reused.")
    stage_facts: list[StageFactsOut]

    @classmethod
    def from_domain(cls, manifest: RunManifest) -> ManifestOut:
        return cls(
            run_id=manifest.run_id,
            review_id=manifest.review_id,
            stages=list(manifest.stages),
            outcome=manifest.outcome,
            created_at=manifest.created_at,
            started_at=manifest.started_at,
            finished_at=manifest.finished_at,
            is_complete=manifest.is_complete,
            vocabulary_versions=list(manifest.vocabulary_versions),
            criteria_versions=list(manifest.criteria_versions),
            plan_versions=list(manifest.plan_versions),
            sources=list(manifest.sources),
            queries=dict(manifest.queries),
            model_ids=list(manifest.model_ids),
            prompt_versions=dict(manifest.prompt_versions),
            papers_from_runs=list(manifest.papers_from_runs),
            stage_facts=[
                StageFactsOut(stage=s.stage, seconds=s.seconds, facts=dict(s.facts))
                for s in manifest.stage_facts
            ],
        )
