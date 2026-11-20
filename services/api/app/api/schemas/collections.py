"""Request and response models for collections, tags, pins, bulk actions and the list.

Every request model forbids unknown fields. Sizes are bounded here; the domain trims and
checks names and tags properly, and its refusal becomes a 422 at the route.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.projects import ProjectOut
from app.application.collections import BulkOutcome, CollectionView, SkipReason
from app.application.project_list import ProjectSummary
from app.domain.collections import MAX_COLLECTION_NAME_LENGTH, MAX_TAG_LENGTH
from app.domain.project_list import ProjectListQuery, ProjectSort
from app.domain.projects import ProjectStatus, Role

# Generous enough for spaces the domain collapses; the domain enforces the real limits.
_Name = Field(min_length=1, max_length=MAX_COLLECTION_NAME_LENGTH * 2)
_Tag = Annotated[str, Field(max_length=MAX_TAG_LENGTH * 2)]
_ProjectIds = Field(min_length=1, max_length=200)
# The value of `collection` that asks for projects in no collection.
UNFILED = "none"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionIn(_Strict):
    name: str = _Name


class CollectionOut(BaseModel):
    id: UUID
    name: str
    project_count: int = Field(
        description="The caller's own projects in it, archived ones aside. Never anyone else's."
    )
    can_manage: bool = Field(description="Whether the caller may rename or delete it.")
    created_at: datetime

    @classmethod
    def from_view(cls, view: CollectionView) -> CollectionOut:
        return cls(
            id=view.id,
            name=view.name,
            project_count=view.project_count,
            can_manage=view.can_manage,
            created_at=view.created_at,
        )


class ProjectCollectionIn(_Strict):
    collection_id: UUID | None = Field(description="null takes the project out of any.")


class ProjectTagsIn(_Strict):
    tags: list[_Tag] = Field(max_length=100)


class MoveProjectsIn(_Strict):
    action: Literal["move"]
    project_ids: list[UUID] = _ProjectIds
    collection_id: UUID | None


class TagProjectsIn(_Strict):
    action: Literal["add_tag"]
    project_ids: list[UUID] = _ProjectIds
    tag: _Tag


class ArchiveProjectsIn(_Strict):
    action: Literal["archive"]
    project_ids: list[UUID] = _ProjectIds


BulkActionIn = Annotated[
    MoveProjectsIn | TagProjectsIn | ArchiveProjectsIn, Field(discriminator="action")
]


class SkippedOut(BaseModel):
    project_id: UUID
    reason: SkipReason = Field(
        description=(
            "not_found also covers a project the caller is not in; not_allowed, one where "
            "their role may not do this; too_many_tags, one already at the tag limit."
        )
    )


class BulkOut(BaseModel):
    changed: list[UUID]
    skipped: list[SkippedOut]

    @classmethod
    def from_outcome(cls, outcome: BulkOutcome) -> BulkOut:
        return cls(
            changed=list(outcome.changed),
            skipped=[SkippedOut(project_id=p, reason=r) for p, r in outcome.skipped],
        )


class ProjectListParams(_Strict):
    sort: ProjectSort = ProjectSort.LAST_ACTIVITY
    status: list[ProjectStatus] = Field(
        default_factory=list, description="Any of these; `archived` brings archived ones in."
    )
    tag: list[_Tag] = Field(
        default_factory=list, max_length=20, description="Every one of these, ignoring case."
    )
    owner: UUID | None = Field(default=None, description="Projects this person owns.")
    member: UUID | None = Field(default=None, description="Projects this person is in.")
    created_from: date | None = Field(default=None, description="Inclusive, in UTC.")
    created_to: date | None = Field(default=None, description="Inclusive, in UTC.")
    collection: UUID | Literal["none"] | None = Field(
        default=None, description=f'A collection id, or "{UNFILED}" for projects in none.'
    )
    include_archived: bool = False

    def to_domain(self) -> ProjectListQuery:
        return ProjectListQuery(
            sort=self.sort,
            statuses=frozenset(self.status),
            tags=tuple(self.tag),
            owner_id=self.owner,
            member_id=self.member,
            created_from=self.created_from,
            created_to=self.created_to,
            collection_id=self.collection if isinstance(self.collection, UUID) else None,
            is_unfiled=self.collection == UNFILED,
            include_archived=self.include_archived,
        )


class MemberRefOut(BaseModel):
    user_id: UUID
    display_name: str
    role: Role


class ProjectSummaryOut(ProjectOut):
    last_activity_at: datetime = Field(
        description="The latest of: created, archived, its newest review, run or brief."
    )
    pinned_at: datetime | None
    members: list[MemberRefOut]
    relevant_papers: int | None = Field(
        description="Included papers of the newest run with papers, under the latest rules."
    )
    papers_found: int | None = Field(description="That run's papers after duplicates.")
    brief_version: int | None

    @classmethod
    def from_summary(cls, summary: ProjectSummary) -> ProjectSummaryOut:
        papers = summary.papers
        return cls(
            **ProjectOut.from_view(summary.project).model_dump(),
            last_activity_at=summary.last_activity_at,
            pinned_at=summary.project.labels.pinned_at,
            members=[
                MemberRefOut(user_id=m.user_id, display_name=m.display_name, role=m.role)
                for m in summary.members
            ],
            relevant_papers=None if papers is None else papers.relevant,
            papers_found=None if papers is None else papers.found,
            brief_version=summary.brief_version,
        )
