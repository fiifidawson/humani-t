"""Seen and saved marks (UI_UX_SPEC §7.5): a person's own, on papers and briefs.

Any member may mark a paper or brief of their project; a non-member gets 404. Marks never
name a project the caller is not a member of. Nothing here is audited: a mark records what
one person read, not a decision about the evidence.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.access import require
from app.api.dependencies import MarksDep
from app.api.schemas.history import (
    PaperMarksOut,
    ProjectPaperMarksOut,
    SavedPaperOut,
    SavedPapersOut,
)
from app.api.session import CurrentDep
from app.application.accounts import Current
from app.domain.marks import PaperMarkKind
from app.domain.projects import Action

router = APIRouter(tags=["marks"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_PAPER = "/projects/{project_id}/papers/{paper_id}"
_NO_PAPER: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such paper in this project"}
}


@router.put(
    f"{_PAPER}/seen",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark a paper as opened by the caller",
    responses=_NO_PAPER,
)
async def see_paper(
    project_id: UUID, paper_id: UUID, current: _Reader, marks: MarksDep
) -> None:
    await marks.mark_paper(current, project_id, paper_id, PaperMarkKind.SEEN)


@router.put(
    f"{_PAPER}/saved",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add a paper to the caller's saved list",
    responses=_NO_PAPER,
)
async def save_paper(
    project_id: UUID, paper_id: UUID, current: _Reader, marks: MarksDep
) -> None:
    await marks.mark_paper(current, project_id, paper_id, PaperMarkKind.SAVED)


@router.delete(
    f"{_PAPER}/saved",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a paper off the caller's saved list",
    responses=_NO_PAPER,
)
async def unsave_paper(
    project_id: UUID, paper_id: UUID, current: _Reader, marks: MarksDep
) -> None:
    await marks.unsave(current, project_id, paper_id)


@router.get(
    "/projects/{project_id}/paper-marks",
    summary="The caller's seen and saved marks on the project's papers",
)
async def read_paper_marks(
    project_id: UUID, current: _Reader, marks: MarksDep
) -> ProjectPaperMarksOut:
    views = await marks.paper_marks(current, project_id)
    return ProjectPaperMarksOut(papers=[PaperMarksOut.from_view(v) for v in views])


@router.get("/saved-papers", summary="The caller's saved papers, newest first")
async def read_saved_papers(current: CurrentDep, marks: MarksDep) -> SavedPapersOut:
    """Only from projects the caller still belongs to; a deleted project's are gone."""
    saved = await marks.saved_papers(current)
    return SavedPapersOut(papers=[SavedPaperOut.from_view(s) for s in saved])


@router.put(
    "/briefs/{brief_id}/seen",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark a brief as opened by the caller, so Needs you stops listing it",
    responses={404: {"description": "No such brief"}},
)
async def see_brief(brief_id: UUID, current: CurrentDep, marks: MarksDep) -> None:
    await marks.mark_brief_read(current, brief_id)
