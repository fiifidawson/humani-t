"""Which of the caller's projects a list shows, and in what order (UI_UX_SPEC §7.2).

Filters only ever narrow the caller's own projects: what the caller may see is decided
before these rules run. Grouping is the page's choice and is not decided here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from app.domain.projects import ProjectStatus


class ProjectSort(StrEnum):
    LAST_ACTIVITY = "last_activity"
    NAME = "name"
    CREATED = "created"
    RELEVANT_PAPERS = "relevant_papers"


@dataclass(frozen=True, slots=True)
class ProjectFacts:
    """What filters and sorting read about one project, in the clear."""

    id: UUID
    name: str
    status: ProjectStatus
    created_at: datetime
    last_activity_at: datetime
    collection_id: UUID | None
    tags: tuple[str, ...]
    owner_ids: frozenset[UUID]
    member_ids: frozenset[UUID]
    relevant_papers: int | None


@dataclass(frozen=True, slots=True)
class ProjectListQuery:
    """Filters, all of which must hold, and one sort order. Empty means "any"."""

    sort: ProjectSort = ProjectSort.LAST_ACTIVITY
    statuses: frozenset[ProjectStatus] = frozenset()
    tags: tuple[str, ...] = ()
    owner_id: UUID | None = None
    member_id: UUID | None = None
    created_from: date | None = None
    created_to: date | None = None
    collection_id: UUID | None = None
    is_unfiled: bool = False
    include_archived: bool = False

    def __post_init__(self) -> None:
        if self.collection_id is not None and self.is_unfiled:
            raise ValueError("ProjectListQuery asks for one collection and for no collection")
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("ProjectListQuery.created_from must not be after created_to")

    @property
    def includes_archived(self) -> bool:
        return self.include_archived or ProjectStatus.ARCHIVED in self.statuses

    def matches(self, project: ProjectFacts) -> bool:
        if project.status is ProjectStatus.ARCHIVED and not self.includes_archived:
            return False
        created = project.created_at.astimezone(UTC).date()
        has = {tag.casefold() for tag in project.tags}
        return (
            (not self.statuses or project.status in self.statuses)
            and all(tag.casefold() in has for tag in self.tags)
            and (self.owner_id is None or self.owner_id in project.owner_ids)
            and (self.member_id is None or self.member_id in project.member_ids)
            and (self.created_from is None or created >= self.created_from)
            and (self.created_to is None or created <= self.created_to)
            and (self.collection_id is None or project.collection_id == self.collection_id)
            and (not self.is_unfiled or project.collection_id is None)
        )

    def ordered(self, projects: Sequence[ProjectFacts]) -> list[ProjectFacts]:
        """Sorted by the chosen order; ties go to the most recent activity."""
        by_activity = sorted(projects, key=lambda p: (p.last_activity_at, p.id), reverse=True)
        match self.sort:
            case ProjectSort.LAST_ACTIVITY:
                return by_activity
            case ProjectSort.NAME:
                return sorted(by_activity, key=lambda p: p.name.casefold())
            case ProjectSort.CREATED:
                return sorted(by_activity, key=lambda p: p.created_at, reverse=True)
            case ProjectSort.RELEVANT_PAPERS:
                return sorted(
                    by_activity,
                    key=lambda p: -1 if p.relevant_papers is None else p.relevant_papers,
                    reverse=True,
                )
