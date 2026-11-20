"""Needs you, Recent activity and a project's timeline (UI_UX_SPEC §7.1, §7.4).

Needs you and activity are the caller's own, across the projects they belong to; the
timeline is one project's, for any member, and a non-member gets 404. All of it is read from
records the pipeline keeps; a dismissal is the only thing written here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, status

from app.api.access import require
from app.api.dependencies import HistoryDep
from app.api.schemas.history import (
    ActivityEventOut,
    ActivityOut,
    DismissalIn,
    NeedsYouOut,
    ProjectHistoryOut,
)
from app.api.session import CurrentDep
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(tags=["history"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]


@router.get("/needs-you", summary="What waits for the caller: gates, drafts, failures, briefs")
async def read_needs_you(current: CurrentDep, history: HistoryDep) -> NeedsYouOut:
    """Only what the caller's role can act on, from live projects that are not archived."""
    return NeedsYouOut.from_view(await history.needs_you(current))


@router.post(
    "/needs-you/dismissals",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hide one Needs you item for the caller until it changes",
    responses={404: {"description": "Nothing waiting for the caller has this key"}},
)
async def dismiss(payload: DismissalIn, current: CurrentDep, history: HistoryDep) -> None:
    await history.dismiss(current, payload.key)


@router.get("/activity", summary="Recent activity in the caller's projects, newest first")
async def read_activity(current: CurrentDep, history: HistoryDep) -> ActivityOut:
    lines = await history.recent_activity(current)
    return ActivityOut(events=[ActivityEventOut.from_view(line) for line in lines])


@router.get(
    "/projects/{project_id}/history",
    summary="Everything that happened in the project, grouped by review",
)
async def read_history(
    project_id: UUID, current: _Reader, history: HistoryDep
) -> ProjectHistoryOut:
    return ProjectHistoryOut.from_view(await history.timeline(current, project_id))
