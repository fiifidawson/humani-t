"""Searching for papers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.access import require
from app.api.dependencies import SearchProviderDep, SearchServiceDep
from app.api.schemas import CompiledQueryOut, PaperOut, SearchIn, SearchOut, SourceOut
from app.api.session import get_current
from app.domain.projects import Action

router = APIRouter(tags=["search"])

# Kept in step with docs/dependencies.md §4. Google Scholar is not a source: it
# has no API and its terms forbid automated queries.
SOURCE_DESCRIPTIONS = {
    "fake": "In-memory corpus of four papers. No network, no key, no rate limit.",
    "openalex": (
        "OpenAlex works index. Answers without a key within a small shared daily "
        "budget; an API key raises the budget and the rate limit."
    ),
    "europepmc": "Europe PMC life-sciences literature.",
    "arxiv": "arXiv preprints. One request every 3 seconds across the deployment.",
}


@router.get(
    "/sources",
    summary="Search sources available in this process",
    dependencies=[Depends(get_current)],
)
async def sources(provider: SearchProviderDep) -> list[SourceOut]:
    """List the repositories this deployment can currently reach."""
    source_id = provider.source_id
    return [
        SourceOut(
            source_id=source_id,
            live=source_id != "fake",
            description=SOURCE_DESCRIPTIONS.get(source_id, "Unknown source."),
        )
    ]


# Inside a project: the results become that project's evidence, and a search is
# a request leaving the deployment on the project's behalf.
@router.post(
    "/projects/{project_id}/search",
    summary="Compile a vocabulary and search for papers",
    dependencies=[require(Action.MANAGE_RUNS)],
)
async def search(payload: SearchIn, service: SearchServiceDep) -> SearchOut:
    """Run a vocabulary against the configured source.

    The compiled query and its provenance come back with the results, because results
    without the query that produced them cannot be audited. It searches the in-memory
    fake corpus, not the sources a run uses.
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
