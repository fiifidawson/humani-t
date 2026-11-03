"""Searching for papers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import SearchProviderDep, SearchServiceDep
from app.api.schemas import CompiledQueryOut, PaperOut, SearchIn, SearchOut, SourceOut

router = APIRouter(tags=["search"])

SOURCE_DESCRIPTIONS = {
    "fake": "In-memory corpus of four papers. No network, no key, no rate limit.",
    "openalex": "OpenAlex works index. Keyless; polite pool via mailto.",
    "arxiv": "arXiv preprints.",
    "europepmc": "Europe PMC life-sciences literature.",
    "scholar": "Google Scholar.",
}


@router.get("/sources", summary="Search sources available in this process")
async def sources(provider: SearchProviderDep) -> list[SourceOut]:
    """Which repositories this deployment can currently reach.

    One entry today. It becomes the list a reviewer checks to know what a run
    did and did not cover — and an empty or unexpected entry here is the first
    sign a run is not reproducible.
    """
    source_id = provider.source_id
    return [
        SourceOut(
            source_id=source_id,
            live=source_id != "fake",
            description=SOURCE_DESCRIPTIONS.get(source_id, "Unknown source."),
        )
    ]


@router.post("/search", summary="Compile a vocabulary and search for papers")
async def search(payload: SearchIn, service: SearchServiceDep) -> SearchOut:
    """Run a vocabulary against the configured source.

    The response carries the compiled query and its provenance alongside the
    results, because a result set separated from the query that produced it
    cannot be audited.

    Currently backed by an in-memory corpus. The endpoint, the request body and
    the response shape do not change when a real source is wired in — only
    `api/dependencies.py` does.
    """
    try:
        vocabulary = payload.to_domain()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    outcome = await service.run(vocabulary, limit=payload.limit)

    return SearchOut(
        source_id=outcome.source_id,
        query=CompiledQueryOut.from_domain(outcome.query),
        total=outcome.total,
        results=[PaperOut.from_domain(r) for r in outcome.records],
    )
