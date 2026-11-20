"""Your view first: the option a member leaned towards before reading a run's brief.

Each member reads and writes only their own answer; a non-member gets the same 404 as a
missing run. Whether the brief canvas asks at all is the project's `is_view_first_on`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import LeaningsDep
from app.api.schemas.leanings import LeaningIn, LeaningOut
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current

router = APIRouter(prefix="/runs/{run_id}/leaning", tags=["briefs"])


async def _run_access(run_id: UUID, current: CurrentDep, leanings: LeaningsDep) -> Current:
    await leanings.authorize(current, run_id)
    return current


_Member = Annotated[Current, Depends(_run_access)]


@router.get("", summary="Which option you leaned towards before reading this run's brief")
async def read_leaning(
    run_id: UUID, current: _Member, leanings: LeaningsDep
) -> LeaningOut | None:
    """`null` until you answer. Nobody else's answer is ever returned."""
    leaning = await leanings.mine(current, run_id)
    return None if leaning is None else LeaningOut.from_domain(leaning)


@router.put(
    "",
    summary="Say which option you lean towards, before reading the brief",
    responses={
        404: {"description": "No such run, or the run has no brief"},
        409: {"description": "Already answered, or the project does not ask"},
    },
)
async def record_leaning(
    run_id: UUID,
    payload: LeaningIn,
    request: Request,
    current: _Member,
    leanings: LeaningsDep,
) -> LeaningOut:
    """Once per member and brief; the first answer stays."""
    try:
        leaning = await leanings.record(current, run_id, payload.option, request_id(request))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return LeaningOut.from_domain(leaning)
