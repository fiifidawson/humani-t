"""Response models for Needs you, Recent activity, the project timeline and marks.

Every line names its project by id and name, and only ever for a project the caller is a
member of. Lines are L0: the facts a one-line summary needs, worded by the browser.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.runs import UserRefOut
from app.application.history import (
    ActivityLine,
    NeedsYouLine,
    ProjectRef,
    ReviewRef,
    TimelineGroup,
)
from app.application.marks import PaperMarksView, ProjectName, SavedPaper
from app.domain.history import (
    MAX_ITEM_KEY_LENGTH,
    ActivityAction,
    ActivityKind,
    NeedsYouKind,
    StageState,
)
from app.domain.runs import GateKind, RunState


class ProjectRefOut(BaseModel):
    id: UUID
    name: str

    @classmethod
    def from_view(cls, project: ProjectRef | ProjectName) -> ProjectRefOut:
        return cls(id=project.id, name=project.name)


class NeedsYouItemOut(BaseModel):
    key: str = Field(description="What `POST /needs-you/dismissals` names. Ids only.")
    kind: NeedsYouKind
    project: ProjectRefOut
    review_id: UUID
    at: datetime = Field(description="When it started waiting.")
    gate_id: UUID | None
    gate_kind: GateKind | None
    run_id: UUID | None
    run_number: int | None = Field(description="The run's number in its review, from 1.")
    stopped_at: str | None = Field(description="The stage a failed run did not finish.")
    version: int | None = Field(description="The draft's plan version, or the brief's.")
    brief_id: UUID | None

    @classmethod
    def from_view(cls, line: NeedsYouLine) -> NeedsYouItemOut:
        item = line.item
        return cls(
            key=item.key,
            kind=item.kind,
            project=ProjectRefOut.from_view(line.project),
            review_id=item.review_id,
            at=item.at,
            gate_id=item.gate_id,
            gate_kind=item.gate_kind,
            run_id=item.run_id,
            run_number=item.run_number,
            stopped_at=item.stopped_at,
            version=item.version,
            brief_id=item.brief_id,
        )


class NeedsYouOut(BaseModel):
    count: int = Field(description="How many items; the rail shows it.")
    items: list[NeedsYouItemOut] = Field(description="Newest first.")

    @classmethod
    def from_view(cls, lines: tuple[NeedsYouLine, ...]) -> NeedsYouOut:
        return cls(count=len(lines), items=[NeedsYouItemOut.from_view(line) for line in lines])


class DismissalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=MAX_ITEM_KEY_LENGTH)


class StageLineOut(BaseModel):
    stage: str
    state: StageState
    seconds: float | None


class ActivityEventOut(BaseModel):
    kind: ActivityKind = Field(description="The timeline filter this line falls under.")
    action: ActivityAction
    at: datetime
    project: ProjectRefOut
    review_id: UUID | None = Field(description="Null for a project-wide line: a document.")
    actor: UserRefOut | None = Field(description="Who did it; null when the model did.")
    run_id: UUID | None
    run_number: int | None
    run_state: RunState | None
    seconds: float | None = Field(description="How long the run has taken so far.")
    stopped_at: str | None
    error: str | None = Field(description="Why a run stopped, in plain words. Timeline only.")
    model_ids: list[str] = Field(description="The models a run, brief or answer used.")
    stages: list[StageLineOut] = Field(description="A run's stages. Timeline only.")
    version: int | None
    brief_id: UUID | None
    thread_id: UUID | None
    document_id: UUID | None
    document_number: int | None
    text: str | None = Field(
        description="The question as stored (masked when masking is on), or a document name."
    )
    papers_moved: int | None

    @classmethod
    def from_view(cls, line: ActivityLine) -> ActivityEventOut:
        event = line.event
        actor = None
        if event.actor_id is not None and line.actor_name is not None:
            actor = UserRefOut(id=event.actor_id, display_name=line.actor_name)
        return cls(
            kind=event.kind,
            action=event.action,
            at=event.at,
            project=ProjectRefOut.from_view(line.project),
            review_id=event.review_id,
            actor=actor,
            run_id=event.run_id,
            run_number=event.run_number,
            run_state=event.run_state,
            seconds=event.seconds,
            stopped_at=event.stopped_at,
            error=event.error,
            model_ids=list(event.model_ids),
            stages=[
                StageLineOut(stage=s.stage, state=s.state, seconds=s.seconds)
                for s in event.stages
            ],
            version=event.version,
            brief_id=event.brief_id,
            thread_id=event.thread_id,
            document_id=event.document_id,
            document_number=event.document_number,
            text=event.text,
            papers_moved=event.papers_moved,
        )


class ActivityOut(BaseModel):
    events: list[ActivityEventOut] = Field(description="Newest first.")


class ReviewRefOut(BaseModel):
    id: UUID
    question: str
    started_at: datetime

    @classmethod
    def from_view(cls, review: ReviewRef) -> ReviewRefOut:
        return cls(id=review.id, question=review.question, started_at=review.started_at)


class HistoryGroupOut(BaseModel):
    review: ReviewRefOut | None = Field(description="Null for project-wide lines.")
    events: list[ActivityEventOut] = Field(description="Newest first.")


class ProjectHistoryOut(BaseModel):
    groups: list[HistoryGroupOut] = Field(description="By latest activity, newest first.")

    @classmethod
    def from_view(cls, groups: tuple[TimelineGroup, ...]) -> ProjectHistoryOut:
        return cls(
            groups=[
                HistoryGroupOut(
                    review=None if g.review is None else ReviewRefOut.from_view(g.review),
                    events=[ActivityEventOut.from_view(line) for line in g.lines],
                )
                for g in groups
            ]
        )


class PaperMarksOut(BaseModel):
    paper_id: UUID
    is_seen: bool = Field(description="Opened here or in another project, by this person.")
    is_saved: bool
    seen_in_other_projects: list[ProjectRefOut] = Field(
        description="Only projects the caller is a member of."
    )

    @classmethod
    def from_view(cls, view: PaperMarksView) -> PaperMarksOut:
        return cls(
            paper_id=view.marks.paper_id,
            is_seen=view.marks.is_seen,
            is_saved=view.marks.is_saved,
            seen_in_other_projects=[
                ProjectRefOut.from_view(p) for p in view.seen_in_other_projects
            ],
        )


class ProjectPaperMarksOut(BaseModel):
    papers: list[PaperMarksOut] = Field(description="Only papers with a mark.")


class SavedPaperOut(BaseModel):
    paper_id: UUID
    project: ProjectRefOut
    review_id: UUID
    title: str
    year: int | None
    doi: str | None
    link: str | None
    saved_at: datetime

    @classmethod
    def from_view(cls, saved: SavedPaper) -> SavedPaperOut:
        return cls(
            paper_id=saved.paper_id,
            project=ProjectRefOut.from_view(saved.project),
            review_id=saved.review_id,
            title=saved.title,
            year=saved.year,
            doi=saved.doi,
            link=saved.link,
            saved_at=saved.saved_at,
        )


class SavedPapersOut(BaseModel):
    papers: list[SavedPaperOut] = Field(description="Newest saved first.")
