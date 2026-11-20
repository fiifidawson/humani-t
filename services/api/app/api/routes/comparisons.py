"""Brief versions, and comparing two versions of a brief or two runs of one review.

Read-only, for any member. Each comparison is computed here, never in the browser, and
reads from the older to the newer. A non-member gets the same 404 as a missing id.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import ComparisonsDep
from app.api.schemas.comparisons import BriefComparisonOut, BriefVersionOut, RunComparisonOut
from app.api.session import CurrentDep

router = APIRouter(tags=["comparisons"])


@router.get(
    "/reviews/{review_id}/brief/versions",
    summary="Every version of a review's brief, newest first",
)
async def list_brief_versions(
    review_id: UUID, current: CurrentDep, comparisons: ComparisonsDep
) -> list[BriefVersionOut]:
    """One version per run of the Brief stage; empty before the first."""
    records = await comparisons.brief_versions(current, review_id)
    latest = max((r.version for r in records), default=0)
    return [BriefVersionOut.from_domain(r, latest) for r in records]


@router.get(
    "/briefs/{brief_id}/compare/{other_brief_id}",
    summary="What changed between two versions of a review's brief",
    responses={422: {"description": "The same version twice, or versions of two reviews"}},
)
async def compare_brief_versions(
    brief_id: UUID, other_brief_id: UUID, current: CurrentDep, comparisons: ComparisonsDep
) -> BriefComparisonOut:
    try:
        compared = await comparisons.compare_briefs(current, brief_id, other_brief_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return BriefComparisonOut.from_compared(compared)


@router.get(
    "/runs/{run_id}/compare/{other_run_id}",
    summary="Two runs of one review side by side: setup, discovery, screening and brief",
    responses={422: {"description": "The same run twice, or runs of two reviews"}},
)
async def compare_runs(
    run_id: UUID, other_run_id: UUID, current: CurrentDep, comparisons: ComparisonsDep
) -> RunComparisonOut:
    """`cannot_compare` says why, and every part is null, when a run has no record."""
    try:
        compared = await comparisons.compare_runs(current, run_id, other_run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return RunComparisonOut.from_domain(compared)
