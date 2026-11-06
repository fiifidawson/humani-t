"""A run's papers, their screening decisions, PRISMA counts and the run manifest.

Read-only routes for every member (viewers included). Each resolves the
project from the review or run it names and answers a non-member with the same
404 as a missing id. Text is served masked; raw kept text never leaves here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import PapersDep
from app.api.schemas.papers import ManifestOut, PaperDetailOut, PaperPageOut, PrismaOut
from app.api.session import CurrentDep
from app.application.papers import LABEL_FILTERS, TIER_FILTERS

router = APIRouter(tags=["papers"])

_RunQuery = Annotated[
    UUID | None,
    Query(description="A run of this review; its newest run with papers if left out."),
]


@router.get("/reviews/{review_id}/papers", summary="A run's papers, most relevant first")
async def list_papers(
    review_id: UUID,
    current: CurrentDep,
    papers: PapersDep,
    run_id: _RunQuery = None,
    label: Annotated[
        str | None, Query(description=f"One of: {', '.join(LABEL_FILTERS)}.")
    ] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    tier: Annotated[
        str | None,
        Query(description=f"One of: {', '.join(TIER_FILTERS)}. Combines with label."),
    ] = None,
) -> PaperPageOut:
    """The screening table: 50 papers a page, with each one's decision and the tier counts."""
    try:
        view = await papers.list_papers(
            current, review_id, run_id=run_id, label=label, page=page, tier=tier
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return PaperPageOut.from_view(view)


@router.get(
    "/reviews/{review_id}/papers/{paper_id}",
    summary="One paper: its decision, provenance and masked text",
)
async def get_paper(
    review_id: UUID,
    paper_id: UUID,
    current: CurrentDep,
    papers: PapersDep,
    run_id: _RunQuery = None,
) -> PaperDetailOut:
    return PaperDetailOut.from_view(
        await papers.paper(current, review_id, paper_id, run_id=run_id)
    )


@router.get("/reviews/{review_id}/prisma", summary="PRISMA-style counts for a run")
async def get_prisma(
    review_id: UUID, current: CurrentDep, papers: PapersDep, run_id: _RunQuery = None
) -> PrismaOut:
    return PrismaOut.from_view(await papers.prisma(current, review_id, run_id=run_id))


@router.get("/runs/{run_id}/manifest", summary="What a run used, as its stages recorded it")
async def get_manifest(run_id: UUID, current: CurrentDep, papers: PapersDep) -> ManifestOut:
    """Vocabulary, criteria and plan versions, sources, queries, models and prompt versions."""
    return ManifestOut.from_domain(await papers.manifest(current, run_id))
