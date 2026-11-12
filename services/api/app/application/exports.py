"""Downloads: gather what one export holds, for a member, and audit that it was taken.

Exports are built on request through the same use cases the screens read, so a file holds
what the screens show and no copy is kept to shred later. Rendering bytes is
`api/exports.py`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import Actor, AuditLog
from app.application.briefs import BriefService, OpenedBrief
from app.application.membership import require_review_member
from app.application.papers import PaperRow, PaperService, PrismaView
from app.domain.accounts import NotFoundError
from app.domain.exports import ExportName
from app.domain.manifest import RunManifest
from app.domain.projects import Action
from app.domain.screening import ScreeningLabel
from app.storage.base import StorageProvider

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

    async def export(
        self,
        current: Current,
        review_id: UUID,
        name: ExportName,
        run_id: UUID | None = None,
        request_id: str | None = None,
    ) -> ExportContent:
        """Gather one export for the review's newest run, or `run_id`; then audit it.

        The default run is the one the matching screen shows by default.
        """
        content = await self._gather(current, review_id, name, run_id)
        async with self._storage.unit_of_work() as uow:
            review = await require_review_member(
                uow, current.user.id, review_id, Action.READ_PROJECT
            )
            await self._audit.record(
                uow,
                Actor(current.user.id, request_id),
                "export.downloaded",
                "review",
                str(review_id),
                scope_id=review.project_id,
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
