"""Request and response models for reviews, runs and gates.

Request models forbid unknown fields, so a client cannot set a status, an owner
or a date by adding it to the body. Pydantic checks shape and size here; the
domain checks meaning (`normalize_question`, `plan_stages`), and its
`ValueError` becomes a 422 in the route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.reviews import ReviewView
from app.domain.reviews import MAX_QUESTION_LENGTH, ReviewStatus
from app.domain.runs import STAGES, Gate, GateDecision, Run, RunState


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewIn(_Strict):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


class UserRefOut(BaseModel):
    """A person as members of the same project see them: never an email address."""

    id: UUID
    display_name: str


class ReviewOut(BaseModel):
    id: UUID
    project_id: UUID
    question: str
    status: ReviewStatus = Field(description="UI_UX_SPEC §11.1: planning, then the latest run.")
    created_at: datetime
    created_by: UserRefOut = Field(description="Who asked the question.")

    @classmethod
    def from_view(cls, view: ReviewView) -> ReviewOut:
        return cls(
            id=view.id,
            project_id=view.project_id,
            question=view.question,
            status=view.status,
            created_at=view.created_at,
            created_by=UserRefOut(id=view.created_by, display_name=view.created_by_name),
        )


class RunIn(_Strict):
    stages: list[str] = Field(
        default_factory=list,
        max_length=len(STAGES),
        description=(
            f"Stage ids, any order; they run in pipeline order: {', '.join(STAGES)}. "
            "Empty or left out: the whole pipeline."
        ),
    )


class RunStartedOut(BaseModel):
    run_id: UUID


class RunOut(BaseModel):
    id: UUID
    review_id: UUID
    stages: list[str]
    state: RunState
    outcome: RunState | None = Field(description="done, failed or cancelled once finished.")
    stages_finished: int
    cancel_requested: bool = Field(description="True once someone asked a running run to stop.")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, run: Run) -> RunOut:
        return cls(
            id=run.id,
            review_id=run.review_id,
            stages=list(run.stages),
            state=run.state,
            outcome=run.outcome,
            stages_finished=run.stages_finished,
            cancel_requested=run.cancel_requested_at is not None,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class GateDecisionIn(_Strict):
    decision: GateDecision
    revisions: dict[str, Any] | None = Field(
        default=None,
        description="What the person changed, e.g. an edited vocabulary. Stored encrypted.",
    )


class GateResolvedOut(BaseModel):
    gate_id: UUID
    decision: GateDecision
    by: UUID = Field(description="The member who decided.")

    @classmethod
    def from_domain(cls, gate: Gate) -> GateResolvedOut:
        if gate.decision is None or gate.resolved_by is None:
            raise ValueError("GateResolvedOut needs a resolved gate")
        return cls(gate_id=gate.id, decision=gate.decision, by=gate.resolved_by)
