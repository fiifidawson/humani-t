"""The All projects list: the caller's projects with what grouping, filters and sort read.

Only projects the caller belongs to are ever read, so no filter, count or group can name
another. Which of them show, and in what order, is `domain/project_list.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.papers import PaperService, PaperTally
from app.application.projects import ProjectService, ProjectView
from app.domain.project_list import ProjectFacts, ProjectListQuery
from app.domain.projects import Role
from app.storage.base import StorageProvider, UnitOfWork


@dataclass(frozen=True, slots=True)
class MemberRef:
    user_id: UUID
    display_name: str
    role: Role


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """One row of the list. `papers` and `brief_version` are `None` before there are any."""

    project: ProjectView
    last_activity_at: datetime
    members: tuple[MemberRef, ...]
    papers: PaperTally | None
    brief_version: int | None

    def facts(self) -> ProjectFacts:
        return ProjectFacts(
            id=self.project.id,
            name=self.project.name,
            status=self.project.status,
            created_at=self.project.created_at,
            last_activity_at=self.last_activity_at,
            collection_id=self.project.labels.collection_id,
            tags=self.project.labels.tags,
            owner_ids=frozenset(m.user_id for m in self.members if m.role is Role.OWNER),
            member_ids=frozenset(m.user_id for m in self.members),
            relevant_papers=None if self.papers is None else self.papers.relevant,
        )


class ProjectListService:
    def __init__(
        self, *, storage: StorageProvider, projects: ProjectService, papers: PaperService
    ) -> None:
        self._storage = storage
        self._projects = projects
        self._papers = papers

    async def list(self, current: Current, query: ProjectListQuery) -> list[ProjectSummary]:
        views = await self._projects.list_mine(
            current, include_archived=query.includes_archived
        )
        async with self._storage.unit_of_work() as uow:
            summaries = {view.id: await self._summary(uow, view) for view in views}
        facts = [s.facts() for s in summaries.values()]
        return [summaries[f.id] for f in query.ordered([f for f in facts if query.matches(f)])]

    async def _summary(self, uow: UnitOfWork, view: ProjectView) -> ProjectSummary:
        members: list[MemberRef] = []
        for membership in await uow.memberships.list_for_project(view.id):
            user = await uow.users.get(membership.user_id)
            name = user.display_name if user is not None else "Unknown account"
            members.append(MemberRef(membership.user_id, name, membership.role))
        times = [view.created_at, *([view.archived_at] if view.archived_at else [])]
        reviews = await uow.reviews.list_for_project(view.id)
        latest = reviews[0] if reviews else None
        papers = brief_version = None
        if latest is not None:
            times.append(latest.created_at)
            runs = await uow.runs.list_for_review(latest.id)
            if runs:
                run = runs[0]
                times += [t for t in (run.created_at, run.started_at, run.finished_at) if t]
            brief = await uow.briefs.latest_for_review(latest.id)
            if brief is not None:
                times.append(brief.created_at)
                brief_version = brief.version
            papers = await self._papers.tally(uow, latest.id)
        return ProjectSummary(view, max(times), tuple(members), papers, brief_version)
