"""Request and response models for follow-up questions and threads.

Responses show the question as it was sent: masked, unless the project's masking was off.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.briefs import AgentStepOut, ClaimOut, PassageOut, ProblemOut
from app.application.questions import AnswerView, QuestionView, ThreadView
from app.domain.briefs import SourceKind
from app.domain.questions import MAX_QUESTION_LENGTH, NOTHING_FOUND, QuestionStatus


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    thread_id: UUID | None = Field(
        default=None, description="Continue this thread; omit to start a new one."
    )


class AnswerSourceOut(BaseModel):
    tag: str
    source_kind: SourceKind
    paper_id: UUID
    passages: list[PassageOut] = Field(description="The masked passages the answer drew on.")


class AnswerOut(BaseModel):
    claims: list[ClaimOut] = Field(description="Up to 6; empty when nothing answers it.")
    nothing_found: bool
    message: str | None = Field(description=f'"{NOTHING_FOUND}" when nothing answers it.')
    run_id: UUID | None = Field(description="The run whose evidence was searched.")
    sources: list[AnswerSourceOut] = Field(description="Every cited tag, by first appearance.")
    steps: list[AgentStepOut] = Field(description="How the answer was made, in order.")
    rounds: int
    kept_round: int
    stopped_because: str | None = Field(description="Null when no model was asked.")
    warnings: list[ProblemOut]

    @classmethod
    def from_view(cls, view: AnswerView) -> AnswerOut:
        cited: dict[str, None] = {}
        for claim in view.answer.claims:
            for citation in claim.citations:
                cited.setdefault(citation.tag)
        sources = []
        for tag in cited:
            found = [r for r in view.evidence if r.passage.tag == tag]
            if not found:
                continue
            first = found[0].passage
            sources.append(
                AnswerSourceOut(
                    tag=tag,
                    source_kind=first.source_kind,
                    paper_id=first.paper_id,
                    passages=[PassageOut.from_domain(r) for r in found],
                )
            )
        nothing = view.answer.nothing_found
        return cls(
            claims=[ClaimOut.from_domain(c) for c in view.answer.claims],
            nothing_found=nothing,
            message=NOTHING_FOUND if nothing else None,
            run_id=view.run_id,
            sources=sources,
            steps=[AgentStepOut.from_domain(s) for s in view.steps],
            rounds=view.rounds,
            kept_round=view.kept_round,
            stopped_because=view.stopped_because,
            warnings=[ProblemOut.from_domain(w) for w in view.warnings],
        )


class QuestionOut(BaseModel):
    id: UUID
    thread_id: UUID
    review_id: UUID
    position: int = Field(description="1 for a thread's first question.")
    status: QuestionStatus = Field(description="pending, answering, answered or failed.")
    asked_by: UUID
    asked_at: datetime
    question_sent: str = Field(description="The question as the model will see it.")
    masked: dict[str, int] = Field(description="What was masked, by type. Never the values.")
    is_masked: bool = Field(
        description=(
            "false when the project's masking was off: the question was sent as written, "
            "apart from the project's own masking rules."
        )
    )
    answered_at: datetime | None
    model_id: str | None
    error: str | None = Field(description="Why it failed, in plain words.")
    answer: AnswerOut | None

    @classmethod
    def from_view(cls, view: QuestionView) -> QuestionOut:
        record = view.record
        return cls(
            id=record.id,
            thread_id=record.thread_id,
            review_id=record.review_id,
            position=record.position,
            status=record.status,
            asked_by=record.asked_by,
            asked_at=record.asked_at,
            question_sent=view.question,
            masked=dict(view.masked),
            is_masked=view.is_masked,
            answered_at=record.answered_at,
            model_id=record.model_id,
            error=record.error,
            answer=None if view.answer is None else AnswerOut.from_view(view.answer),
        )


class ThreadOut(BaseModel):
    id: UUID
    review_id: UUID
    created_at: datetime
    created_by: UUID
    questions: list[QuestionOut] = Field(description="In the order they were asked.")

    @classmethod
    def from_view(cls, view: ThreadView) -> ThreadOut:
        thread = view.thread
        return cls(
            id=thread.id,
            review_id=thread.review_id,
            created_at=thread.created_at,
            created_by=thread.created_by,
            questions=[QuestionOut.from_view(q) for q in view.questions],
        )
