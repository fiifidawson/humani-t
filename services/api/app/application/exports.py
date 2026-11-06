"""Downloads: gather what one export holds, for a member, and audit that it was taken.

Every export is generated on request from what is already stored, through the
same use cases the screens read (`PaperService`, `BriefService`), so a file
holds exactly what the screens show and nothing is kept twice: deleting the
project leaves no export copy behind to shred. Rendering the bytes is the
API boundary's job (`api/exports.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import Actor, AuditLog
from app.application.briefs import BriefService, OpenedBrief
from app.application.membership import require_member
from app.application.papers import PaperRow, PaperService, PrismaView
from app.domain.accounts import NotFoundError
from app.domain.exports import ExportName
from app.domain.manifest import RunManifest
from app.domain.projects import Action
from app.domain.screening import ScreeningLabel
from app.storage.base import StorageProvider

_REVIEW_NOT_FOUND = "No such review."
_RUN_NOT_FOUND = "No such run."


@dataclass(frozen=True, slots=True)
class ExportContent:
    """What one export holds. Only the part its name needs is set."""

    name: ExportName
    run_id: UUID
    rows: tuple[PaperRow, ...] = ()
    prisma: PrismaView | None = None
    manifest: RunManifest | None = None
    brief: OpenedBrief | None = None


class ExportService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        papers: PaperService,
        briefs: BriefService,
        audit: AuditLog,
    ) -> None:
        self._storage = storage
        self._papers = papers
        self._briefs = briefs
        self._audit = audit

    async def authorize_review(self, current: Current, review_id: UUID) -> None:
        """Check access without exporting, so the HTTP layer refuses before anything else."""
        await self._project_of(current, review_id)

    async def export(
        self,
        current: Current,
        review_id: UUID,
        name: ExportName,
        run_id: UUID | None = None,
        request_id: str | None = None,
    ) -> ExportContent:
        """Gather one export for the review's newest run, or `run_id`; then audit it.

        The default run is the one each screen shows by default: the newest
        with papers for the dataset, PRISMA and manifest, the newest brief for
        the brief.
        """
        content = await self._gather(current, review_id, name, run_id)
        project_id = await self._project_of(current, review_id)
        async with self._storage.unit_of_work() as uow:
            await self._audit.record(
                uow,
                Actor(current.user.id, request_id),
                "export.downloaded",
                "review",
                str(review_id),
                scope_id=project_id,
                details={"export": name.value, "run_id": str(content.run_id)},
            )
            await uow.commit()
        return content

    async def _gather(
        self, current: Current, review_id: UUID, name: ExportName, run_id: UUID | None
    ) -> ExportContent:
        if name.is_brief:
            brief = await self._briefs.for_review(current, review_id, run_id)
            return ExportContent(name, brief.brief.run_id, brief=brief)
        if name is ExportName.MANIFEST_JSON:
            chosen = run_id or (await self._papers.prisma(current, review_id)).run_id
            manifest = await self._papers.manifest(current, chosen)
            if manifest.review_id != review_id:
                raise NotFoundError(_RUN_NOT_FOUND)
            return ExportContent(name, chosen, manifest=manifest)
        if name is ExportName.PRISMA_MD:
            prisma = await self._papers.prisma(current, review_id, run_id=run_id)
            return ExportContent(name, prisma.run_id, prisma=prisma)
        label = ScreeningLabel.INCLUDE.value if name is ExportName.INCLUDED_RIS else None
        rows, chosen = await self._all_rows(current, review_id, run_id, label)
        return ExportContent(name, chosen, rows=rows)

    async def _all_rows(
        self, current: Current, review_id: UUID, run_id: UUID | None, label: str | None
    ) -> tuple[tuple[PaperRow, ...], UUID]:
        """Every row of the screening table, in its order, one page per transaction."""
        page = await self._papers.list_papers(current, review_id, run_id=run_id, label=label)
        rows = list(page.rows)
        number = 1
        while len(rows) < page.total:
            number += 1
            page = await self._papers.list_papers(
                current, review_id, run_id=page.run_id, label=label, page=number
            )
            if not page.rows:
                break
            rows.extend(page.rows)
        return tuple(rows), page.run_id

    async def _project_of(self, current: Current, review_id: UUID) -> UUID:
        async with self._storage.unit_of_work() as uow:
            review = await uow.reviews.get(review_id)
            if review is None:
                raise NotFoundError(_REVIEW_NOT_FOUND)
            await require_member(
                uow,
                current.user.id,
                review.project_id,
                Action.READ_PROJECT,
                not_found=_REVIEW_NOT_FOUND,
            )
            return review.project_id
