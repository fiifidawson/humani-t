"""Seen and saved marks: record what a person opened or saved, and show it wherever it appears.

A paper is matched across projects by `PaperRecord.identity` (DOI first, else title and
year), the dedup rule's own key. A mark keeps that identity sealed under its paper's project
key; to find the paper in another project it is digested under that project's identity key,
the same lookup a re-run uses. Marks in projects the caller has left or that were deleted are
never opened, so they can say nothing about those projects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.papers import PaperStore
from app.application.projects import name_context
from app.domain.accounts import NotFoundError
from app.domain.discovery import StoredPaper, identity_digest
from app.domain.encryption import EncryptionContext
from app.domain.marks import BriefRead, PaperMark, PaperMarkKind, PaperMarks, marks_on_papers
from app.domain.projects import Action, Project
from app.domain.retention import READ_COUNTS_EVERY
from app.storage.base import StorageProvider, UnitOfWork

# Table, column; the row is the person, the paper and the kind of mark.
_IDENTITY_PURPOSE = "paper_marks.identity"
_NO_PAPER = "No such paper."
_NO_BRIEF = "No such brief."
_NO_PROJECT = "No such project."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identity_context(
    mark_project: UUID, user_id: UUID, paper_id: UUID, kind: str
) -> EncryptionContext:
    return EncryptionContext(mark_project, _IDENTITY_PURPOSE, f"{user_id}:{paper_id}:{kind}")


@dataclass(frozen=True, slots=True)
class ProjectName:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class PaperMarksView:
    marks: PaperMarks
    seen_in_other_projects: tuple[ProjectName, ...]


@dataclass(frozen=True, slots=True)
class SavedPaper:
    paper_id: UUID
    project: ProjectName
    review_id: UUID
    title: str
    year: int | None
    doi: str | None
    link: str | None
    saved_at: datetime


class MarkService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        store: PaperStore,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._store = store
        self._clock = clock

    async def mark_paper(
        self, current: Current, project_id: UUID, paper_id: UUID, kind: PaperMarkKind
    ) -> None:
        """Mark a paper of the project seen or saved. Marking again keeps the first time."""
        async with self._storage.unit_of_work() as uow:
            paper = await _paper_of(uow, current, project_id, paper_id)
            record = await self._store.open_paper(uow, paper)
            sealed = await self._encryption.encrypt(
                uow,
                _identity_context(project_id, current.user.id, paper_id, kind.value),
                record.identity.encode(),
            )
            now = self._clock()
            mark = PaperMark(current.user.id, paper_id, kind, project_id, sealed, now)
            is_new = await uow.marks.add_paper_mark(mark)
            if await _read(uow, project_id, now) or is_new:
                await uow.commit()

    async def unsave(self, current: Current, project_id: UUID, paper_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await _paper_of(uow, current, project_id, paper_id)
            if await uow.marks.remove_paper_mark(
                current.user.id, paper_id, PaperMarkKind.SAVED
            ):
                await uow.commit()

    async def mark_brief_read(self, current: Current, brief_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            brief = await uow.briefs.get(brief_id)
            if brief is None:
                raise NotFoundError(_NO_BRIEF)
            await require_member(
                uow, current.user.id, brief.project_id, Action.READ_PROJECT, not_found=_NO_BRIEF
            )
            now = self._clock()
            read = BriefRead(current.user.id, brief.id, brief.project_id, now)
            is_new = await uow.marks.add_brief_read(read)
            if await _read(uow, brief.project_id, now) or is_new:
                await uow.commit()

    async def paper_marks(
        self, current: Current, project_id: UUID
    ) -> tuple[PaperMarksView, ...]:
        """The caller's marks on the project's papers, for every paper that has one."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            visible = await _visible_projects(uow, current.user.id)
            marks = [
                m
                for m in await uow.marks.list_paper_marks(current.user.id)
                if m.project_id in visible
            ]
            seen_where: dict[str, set[UUID]] = {}
            for mark in marks:
                if mark.kind is PaperMarkKind.SEEN:
                    identity = await self._identity(uow, mark)
                    seen_where.setdefault(identity, set()).add(mark.project_id)
            saved = {
                m.paper_id
                for m in marks
                if m.kind is PaperMarkKind.SAVED and m.project_id == project_id
            }
            seen_in = await self._seen_in(uow, project_id, seen_where)
            names: dict[UUID, ProjectName] = {}
            views: list[PaperMarksView] = []
            for paper_marks in marks_on_papers(project_id, seen_in, saved):
                others = [
                    await self._name(uow, visible[p], names)
                    for p in paper_marks.seen_in_other_projects
                ]
                views.append(
                    PaperMarksView(paper_marks, tuple(sorted(others, key=lambda n: n.name)))
                )
            return tuple(views)

    async def saved_papers(self, current: Current) -> tuple[SavedPaper, ...]:
        """The caller's saved papers from projects they still belong to, newest first."""
        async with self._storage.unit_of_work() as uow:
            visible = await _visible_projects(uow, current.user.id)
            names: dict[UUID, ProjectName] = {}
            saved: list[SavedPaper] = []
            for mark in await uow.marks.list_paper_marks(current.user.id):
                if mark.kind is not PaperMarkKind.SAVED or mark.project_id not in visible:
                    continue
                paper = await uow.papers.get(mark.paper_id)
                if paper is None:
                    continue
                record = await self._store.open_paper(uow, paper)
                saved.append(
                    SavedPaper(
                        paper_id=paper.id,
                        project=await self._name(uow, visible[mark.project_id], names),
                        review_id=paper.review_id,
                        title=record.title,
                        year=record.year,
                        doi=record.doi,
                        link=record.link,
                        saved_at=mark.marked_at,
                    )
                )
        return tuple(sorted(saved, key=lambda s: s.saved_at, reverse=True))

    async def _seen_in(
        self, uow: UnitOfWork, project_id: UUID, seen_where: dict[str, set[UUID]]
    ) -> dict[UUID, set[UUID]]:
        """Each of the project's papers the caller has seen anywhere, with where."""
        if not seen_where:
            return {}
        key = await self._store.identity_key(uow, project_id)
        by_digest = {identity_digest(key, identity): identity for identity in seen_where}
        seen_in: dict[UUID, set[UUID]] = {}
        for review in await uow.reviews.list_for_project(project_id):
            for paper in await uow.papers.find_by_identity(review.id, list(by_digest)):
                seen_in[paper.id] = seen_where[by_digest[paper.identity_digest]]
        return seen_in

    async def _identity(self, uow: UnitOfWork, mark: PaperMark) -> str:
        context = _identity_context(
            mark.project_id, mark.user_id, mark.paper_id, mark.kind.value
        )
        return (await self._encryption.decrypt(uow, context, mark.identity_sealed)).decode()

    async def _name(
        self, uow: UnitOfWork, project: Project, names: dict[UUID, ProjectName]
    ) -> ProjectName:
        if project.id not in names:
            name = await self._encryption.decrypt(
                uow, name_context(project.id), project.name_sealed
            )
            names[project.id] = ProjectName(project.id, name.decode())
        return names[project.id]


async def _read(uow: UnitOfWork, project_id: UUID, now: datetime) -> bool:
    """Count an opened paper or brief as activity on the retention clock (ADR 0020)."""
    return await uow.projects.touch_read(project_id, now, unless_since=now - READ_COUNTS_EVERY)


async def _paper_of(
    uow: UnitOfWork, current: Current, project_id: UUID, paper_id: UUID
) -> StoredPaper:
    await require_member(
        uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PAPER
    )
    paper = await uow.papers.get(paper_id)
    if paper is None or paper.project_id != project_id:
        raise NotFoundError(_NO_PAPER)
    return paper


async def _visible_projects(uow: UnitOfWork, user_id: UUID) -> dict[UUID, Project]:
    """The live projects the caller belongs to now; archived ones are still theirs."""
    return {p.id: p for p in await uow.projects.list_for_member(user_id)}
