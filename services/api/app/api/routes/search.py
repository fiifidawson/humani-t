"""Search across every project the caller belongs to (UI_UX_SPEC §7.6, ADR 0018).

No project in the path: the use case reads only the caller's own projects, so a
non-member's project never shows, not even as an empty group.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import SearchDep
from app.api.schemas.search import SearchOut
from app.api.session import CurrentDep
from app.domain.project_search import SearchQuery, SearchScope

router = APIRouter(prefix="/search", tags=["search"])

# Generous on purpose: the domain trims, counts and names the field it refuses.
_MAX_RAW_LENGTH = 1000


@router.get(
    "",
    summary="Find projects, briefs, papers, passages and questions in your projects",
    responses={429: {"description": "Too many searches lately; see Retry-After"}},
)
async def search(
    current: CurrentDep,
    finder: SearchDep,
    q: Annotated[str, Query(max_length=_MAX_RAW_LENGTH, description="2 to 200 characters.")],
    scope: Annotated[
        SearchScope | None, Query(description="Leave out for every scope.")
    ] = None,
) -> SearchOut:
    try:
        query = SearchQuery(q, scope)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return SearchOut.from_domain(query.text, scope, await finder.search(current, query))
