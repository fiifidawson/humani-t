"""Reading a decision brief: the brief, one cited source, and how it was made.

Read-only, for any member of the brief's project. A non-member gets the same 404 as
a missing id. Briefs are written by the `answer` stage of a run, never here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.api.dependencies import BriefsDep
from app.api.schemas.briefs import BriefOut, EvidenceOut, TraceOut
from app.api.session import CurrentDep

router = APIRouter(tags=["briefs"])

_Tag = Annotated[str, Path(min_length=1, max_length=64, description="A cited tag, e.g. `P12`.")]


@router.get(
    "/reviews/{review_id}/brief",
    summary="A review's decision brief: the latest, or one run's",
    responses={404: {"description": "No such review, or no brief yet"}},
)
async def read_brief(
    review_id: UUID,
    current: CurrentDep,
    briefs: BriefsDep,
    run_id: Annotated[
        UUID | None, Query(description="The run whose brief to return; the latest if omitted.")
    ] = None,
) -> BriefOut:
    return BriefOut.from_domain(await briefs.for_review(current, review_id, run_id))


@router.get(
    "/briefs/{brief_id}/evidence/{tag}",
    summary="One cited source: its masked passages, details and the claims citing it",
    responses={404: {"description": "No such brief, or no passage with this tag"}},
)
async def read_evidence(
    brief_id: UUID, tag: _Tag, current: CurrentDep, briefs: BriefsDep
) -> EvidenceOut:
    return EvidenceOut.from_domain(await briefs.cited_source(current, brief_id, tag))


@router.get(
    "/briefs/{brief_id}/trace",
    summary="How the brief was made: every agent step, in order",
    response_model_exclude_unset=True,
)
async def read_trace(brief_id: UUID, current: CurrentDep, briefs: BriefsDep) -> TraceOut:
    """Each step is the `agent.step` event the run streamed; absent fields are omitted."""
    return TraceOut.from_domain(await briefs.open(current, brief_id))
