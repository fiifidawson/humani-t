"""Response models for reading a decision brief.

No field ranks the options or totals a score, because the brief never does.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.briefs import CitedSource, OpenedBrief
from app.domain.briefs import AgentRole as _AgentRole
from app.domain.briefs import (
    AgentStep,
    Claim,
    ClaimPlace,
    ClaimSupport,
    ConflictType,
    CriticScores,
    EvidenceStrength,
    Problem,
    RetrievedPassage,
    SourceDetails,
    SourceKind,
    StepStatus,
    StopReason,
)


class CitationOut(BaseModel):
    tag: str = Field(description="`P1` for a paper, `D1` for a team document.")
    source_kind: SourceKind


class ClaimOut(BaseModel):
    text: str
    support: ClaimSupport = Field(
        description="evidence: the cited passages say this; hypothesis: the model's reasoning."
    )
    citations: list[CitationOut] = Field(description="Empty for a hypothesis.")

    @classmethod
    def from_domain(cls, claim: Claim) -> ClaimOut:
        return cls(
            text=claim.text,
            support=claim.support,
            citations=[
                CitationOut(tag=c.tag, source_kind=c.source_kind) for c in claim.citations
            ],
        )


class OptionOut(BaseModel):
    name: str
    description: str
    strength: EvidenceStrength
    benefits: list[ClaimOut]
    risks: list[ClaimOut]


class AssessmentOut(BaseModel):
    option: str
    claim: ClaimOut


class TradeOffOut(BaseModel):
    criterion: str
    is_humanitarian: bool = Field(description="One of the humanitarian principles.")
    assessments: list[AssessmentOut] = Field(description="One per option, in option order.")


class ConflictOut(BaseModel):
    question: str
    conflict_type: ConflictType
    positions: list[ClaimOut]
    meaning: ClaimOut | None = Field(description="What it means; always reasoning.")


class ConsiderationOut(BaseModel):
    criterion: str
    option: str
    because: ClaimOut


class SourceDetailsOut(BaseModel):
    title: str
    year: int | None
    doi: str | None
    url: str | None
    screening_label: str | None
    rationale: str | None = Field(description="Why screening included it; null if unknown.")

    @classmethod
    def from_domain(cls, details: SourceDetails) -> SourceDetailsOut:
        return cls(
            title=details.title,
            year=details.year,
            doi=details.doi,
            url=details.url,
            screening_label=details.screening_label,
            rationale=details.rationale,
        )


class SourceOut(BaseModel):
    tag: str
    source_kind: SourceKind | None = Field(description="Null if no passage has this tag.")
    paper_id: UUID | None
    has_passage: bool = Field(description="False only for a citation the checks flagged.")
    title: str | None
    year: int | None
    doi: str | None
    url: str | None
    screening_label: str | None
    citations: int = Field(description="How many times the brief cites this tag.")


class ProblemOut(BaseModel):
    code: str
    message: str
    where: str | None = Field(description="The claim or section, e.g. `options/0/benefits/1`.")

    @classmethod
    def from_domain(cls, problem: Problem) -> ProblemOut:
        return cls(code=problem.code, message=problem.message, where=problem.where)


class QualityOut(BaseModel):
    """The Critic's scores, 0 to 2 each. Deliberately not summed."""

    coverage: int
    groundedness: int
    disagreements: int
    uncertainty: int
    humanitarian_awareness: int
    specificity: int

    @classmethod
    def from_domain(cls, scores: CriticScores) -> QualityOut:
        return cls.model_validate(scores.to_payload())


class BriefOut(BaseModel):
    id: UUID
    review_id: UUID
    run_id: UUID
    version: int = Field(description="1 for the review's first brief; one per run.")
    created_at: datetime
    question: str = Field(description="As the model saw it: masked.")
    caveat: str
    bottom_line: list[ClaimOut]
    options: list[OptionOut] = Field(description="2 to 4, in the Writer's order. No ranking.")
    trade_offs: list[TradeOffOut]
    conflicts: list[ConflictOut]
    considerations: list[ConsiderationOut]
    gaps: list[str]
    sources: list[SourceOut] = Field(description="Every cited tag, by first appearance.")
    quality: QualityOut | None
    warnings: list[ProblemOut] = Field(description="Checks still failing on the kept draft.")
    model_id: str
    rounds: int
    kept_round: int = Field(description="The round whose Writer step wrote this brief.")
    max_rounds: int
    stopped_because: StopReason

    @classmethod
    def from_domain(cls, opened: OpenedBrief) -> BriefOut:
        brief, body = opened.brief, opened.brief.body
        return cls(
            id=brief.id,
            review_id=brief.review_id,
            run_id=brief.run_id,
            version=brief.version,
            created_at=brief.created_at,
            question=brief.question,
            caveat=brief.caveat,
            bottom_line=[ClaimOut.from_domain(c) for c in body.bottom_line],
            options=[
                OptionOut(
                    name=o.name,
                    description=o.description,
                    strength=o.strength,
                    benefits=[ClaimOut.from_domain(c) for c in o.benefits],
                    risks=[ClaimOut.from_domain(c) for c in o.risks],
                )
                for o in body.options
            ],
            trade_offs=[
                TradeOffOut(
                    criterion=row.criterion,
                    is_humanitarian=row.is_humanitarian,
                    assessments=[
                        AssessmentOut(option=a.option, claim=ClaimOut.from_domain(a.claim))
                        for a in row.assessments
                    ],
                )
                for row in body.trade_offs
            ],
            conflicts=[
                ConflictOut(
                    question=c.question,
                    conflict_type=c.conflict_type,
                    positions=[ClaimOut.from_domain(p) for p in c.positions],
                    meaning=None if c.meaning is None else ClaimOut.from_domain(c.meaning),
                )
                for c in body.conflicts
            ],
            considerations=[
                ConsiderationOut(
                    criterion=c.criterion,
                    option=c.option,
                    because=ClaimOut.from_domain(c.because),
                )
                for c in body.considerations
            ],
            gaps=[g.text for g in body.gaps],
            sources=_sources(opened),
            quality=None if brief.quality is None else QualityOut.from_domain(brief.quality),
            warnings=[ProblemOut.from_domain(w) for w in brief.warnings],
            model_id=brief.model_id,
            rounds=brief.rounds,
            kept_round=brief.kept_round,
            max_rounds=brief.max_rounds,
            stopped_because=brief.stopped_because,
        )


def _sources(opened: OpenedBrief) -> list[SourceOut]:
    counts: dict[str, int] = {}
    for _, claim in opened.brief.body.claims():
        for citation in claim.citations:
            counts[citation.tag] = counts.get(citation.tag, 0) + 1
    first_passage = {}
    for item in opened.evidence:
        first_passage.setdefault(item.passage.tag, item.passage)
    sources = []
    for tag in opened.brief.body.cited_tags():
        passage = first_passage.get(tag)
        details = None if passage is None else opened.sources.get(passage.paper_id)
        sources.append(
            SourceOut(
                tag=tag,
                source_kind=None if passage is None else passage.source_kind,
                paper_id=None if passage is None else passage.paper_id,
                has_passage=passage is not None,
                title=None if details is None else details.title,
                year=None if details is None else details.year,
                doi=None if details is None else details.doi,
                url=None if details is None else details.url,
                screening_label=None if details is None else details.screening_label,
                citations=counts[tag],
            )
        )
    return sources


class PassageOut(BaseModel):
    text: str = Field(description="The exact masked text the Writer saw.")
    locator: str = Field(description="Which part of the source, e.g. `abstract`.")
    found_by: str = Field(description="The search that retrieved it.")
    round: int = Field(description="The round of the Retriever step that found it.")

    @classmethod
    def from_domain(cls, item: RetrievedPassage) -> PassageOut:
        return cls(
            text=item.passage.text,
            locator=item.passage.locator,
            found_by=item.found_by,
            round=item.round_number,
        )


class CitedForOut(BaseModel):
    path: str = Field(description="Where the claim sits, e.g. `options/0/risks/1`.")
    section: str = Field(
        description="bottom_line, benefit, risk, trade_off, conflict or consideration."
    )
    option: str | None
    criterion: str | None
    text: str
    support: ClaimSupport

    @classmethod
    def from_domain(cls, place: ClaimPlace, claim: Claim) -> CitedForOut:
        return cls(
            path=place.path,
            section=place.section,
            option=place.option,
            criterion=place.criterion,
            text=claim.text,
            support=claim.support,
        )


class EvidenceOut(BaseModel):
    tag: str
    source_kind: SourceKind
    paper_id: UUID
    source: SourceDetailsOut | None = Field(description="Null when the pipeline had none.")
    passages: list[PassageOut]
    cited_for: list[CitedForOut] = Field(description="Every claim citing this tag.")

    @classmethod
    def from_domain(cls, cited: CitedSource) -> EvidenceOut:
        return cls(
            tag=cited.tag,
            source_kind=cited.source_kind,
            paper_id=cited.paper_id,
            source=None
            if cited.details is None
            else SourceDetailsOut.from_domain(cited.details),
            passages=[PassageOut.from_domain(p) for p in cited.passages],
            cited_for=[
                CitedForOut.from_domain(place, claim) for place, claim in cited.cited_for
            ],
        )


class TokensOut(BaseModel):
    prompt: int
    completion: int


class AgentStepOut(BaseModel):
    """One `agent.step`, exactly as the run's stream sent it; absent fields are omitted."""

    round: int = Field(description="0 for the Planner and the first retrieval.")
    role: _AgentRole
    status: StepStatus
    seconds: float
    tokens: TokensOut | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    queries: list[str] | None = None
    passages_found: int | None = None
    passages_new: int | None = None
    passages_total: int | None = None
    problems: list[ProblemOut] | None = Field(default=None, description="Checks steps only.")

    @classmethod
    def from_domain(cls, step: AgentStep) -> AgentStepOut:
        return cls.model_validate(step.to_payload())


class TotalsOut(BaseModel):
    calls: int = Field(description="Model calls made, refused prompts excluded.")
    prompt_tokens: int
    completion_tokens: int


class TraceOut(BaseModel):
    brief_id: UUID
    model_id: str
    rounds: int
    kept_round: int
    max_rounds: int
    stopped_because: StopReason
    steps: list[AgentStepOut]
    totals: TotalsOut
    manifest: dict[str, Any] = Field(description="Model id and prompt version per role.")
    is_masked: bool | None = Field(
        description=(
            "Whether the brief's text was masked (ADR 0014); null for a brief written "
            "before this was recorded."
        )
    )

    @classmethod
    def from_domain(cls, opened: OpenedBrief) -> TraceOut:
        brief, steps = opened.brief, opened.steps
        return cls(
            brief_id=brief.id,
            model_id=brief.model_id,
            rounds=brief.rounds,
            kept_round=brief.kept_round,
            max_rounds=brief.max_rounds,
            stopped_because=brief.stopped_because,
            steps=[AgentStepOut.from_domain(s) for s in steps],
            totals=TotalsOut(
                calls=sum(1 for s in steps if s.is_model_call),
                prompt_tokens=sum(s.prompt_tokens or 0 for s in steps),
                completion_tokens=sum(s.completion_tokens or 0 for s in steps),
            ),
            manifest={k: v for k, v in opened.manifest.items() if k != "is_masked"},
            is_masked=_is_masked(opened.manifest),
        )


def _is_masked(manifest: Mapping[str, Any]) -> bool | None:
    value = manifest.get("is_masked")
    return value if isinstance(value, bool) else None
