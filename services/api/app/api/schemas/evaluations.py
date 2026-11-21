"""Evaluation payloads: the upload, the judge models on offer, and the results.

The files travel as text inside JSON: the browser reads them and the domain parses them,
so the upload format is checked in one place (`domain/evaluations.py`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas.briefs import QualityOut
from app.application.evaluations import EvaluationView
from app.domain.evaluations import (
    MAX_OTHER_SYSTEMS,
    MAX_SYSTEM_NAME_LENGTH,
    EvaluationRecord,
    EvaluationStatus,
    EvaluationUpload,
    PairResult,
    Verdict,
)

# Characters, not bytes: 50 questions of 2,000 characters, and room for answers of 8,000.
MAX_QUESTION_FILE_LENGTH = 200_000
MAX_ANSWER_FILE_LENGTH = 500_000


class OtherSystemIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_SYSTEM_NAME_LENGTH)
    answers_file: str = Field(
        max_length=MAX_ANSWER_FILE_LENGTH,
        description='JSON Lines: one {"answer": "…"} per line, in the questions\' order.',
    )


class EvaluationIn(BaseModel):
    project_id: UUID = Field(description="A project the administrator is a member of.")
    judge_model: str = Field(min_length=1, max_length=256)
    question_file: str = Field(
        max_length=MAX_QUESTION_FILE_LENGTH, description="Plain text: one question per line."
    )
    other_systems: list[OtherSystemIn] = Field(default=[], max_length=MAX_OTHER_SYSTEMS)

    def to_domain(self) -> EvaluationUpload:
        """Raises `ValueError`, naming the line, for a file the rules refuse."""
        return EvaluationUpload.from_files(
            self.project_id,
            self.judge_model,
            self.question_file,
            [(s.name, s.answers_file) for s in self.other_systems],
        )


class JudgeModelsOut(BaseModel):
    writer_model: str = Field(description="The model that writes humani-t's answers.")
    judge_models: list[str] = Field(
        description="Only models the operator configured; the writer's own is first."
    )


class EvaluationSummaryOut(BaseModel):
    id: UUID
    project_id: UUID
    status: EvaluationStatus
    judge_model: str
    writer_model: str | None = Field(description="Known once the first question is answered.")
    question_count: int
    answered_count: int = Field(description="Questions answered and judged so far.")
    system_count: int = Field(description="Other systems compared with humani-t.")
    created_at: datetime
    finished_at: datetime | None
    error: str | None

    @classmethod
    def from_record(cls, record: EvaluationRecord) -> EvaluationSummaryOut:
        return cls(
            id=record.id,
            project_id=record.project_id,
            status=record.status,
            judge_model=record.judge_model,
            writer_model=record.writer_model,
            question_count=record.question_count,
            answered_count=record.answered_count,
            system_count=record.system_count,
            created_at=record.created_at,
            finished_at=record.finished_at,
            error=record.error,
        )


class MetricOut(BaseModel):
    metric: str
    first_mean: float
    second_mean: float
    difference: float = Field(description="first_mean minus second_mean.")


class PairResultOut(BaseModel):
    """Two systems over every question judged so far, from the first system's side."""

    first: str
    second: str
    judged: int
    metrics: list[MetricOut]
    wins: int
    ties: int
    losses: int

    @classmethod
    def from_domain(cls, pair: PairResult) -> PairResultOut:
        return cls(
            first=pair.first,
            second=pair.second,
            judged=pair.judged,
            metrics=[
                MetricOut(
                    metric=m.metric,
                    first_mean=round(m.first_mean, 2),
                    second_mean=round(m.second_mean, 2),
                    difference=round(m.difference, 2),
                )
                for m in pair.metrics
            ],
            wins=pair.wins,
            ties=pair.ties,
            losses=pair.losses,
        )


class AnswerOut(BaseModel):
    position: int
    question: str = Field(description="As stored and sent: masked when masking is on.")
    answer: str = Field(description="humani-t's answer, as the judge read it.")


class JudgementOut(BaseModel):
    position: int
    question: str
    system_a: str = Field(description="Which system wrote answer A. The judge never saw it.")
    system_b: str
    answer_a: str
    answer_b: str
    scores_a: QualityOut
    scores_b: QualityOut
    winner: Verdict = Field(description="a, b or tie, as the judge said it.")
    reason: str


class EvaluationOut(EvaluationSummaryOut):
    systems: list[str] = Field(description="humani-t first, then the other systems.")
    is_masked: bool = Field(description="Whether the upload was masked before it was stored.")
    masked: dict[str, int] = Field(description="Masked values by type, over the whole upload.")
    judge_prompt_version: str
    results: list[PairResultOut]
    answers: list[AnswerOut]
    judgements: list[JudgementOut]

    @classmethod
    def from_view(cls, view: EvaluationView) -> EvaluationOut:
        summary = EvaluationSummaryOut.from_record(view.record)
        return cls(
            **summary.model_dump(),
            systems=list(view.systems),
            is_masked=view.setup.is_masked,
            masked=dict(view.setup.masked),
            judge_prompt_version=view.setup.prompt_version,
            results=[PairResultOut.from_domain(p) for p in view.results],
            answers=[
                AnswerOut(
                    position=a.position, question=view.question(a.position), answer=a.answer
                )
                for a in view.answered
            ],
            judgements=[
                JudgementOut(
                    position=j.position,
                    question=view.question(j.position),
                    system_a=j.system_a,
                    system_b=j.system_b,
                    answer_a=view.answer_of(j.system_a, j.position),
                    answer_b=view.answer_of(j.system_b, j.position),
                    scores_a=QualityOut.from_domain(j.scores_a),
                    scores_b=QualityOut.from_domain(j.scores_b),
                    winner=j.verdict,
                    reason=j.reason,
                )
                for j in view.judgements
            ],
        )
