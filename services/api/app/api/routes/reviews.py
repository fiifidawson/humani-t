"""Reviews inside a project.

Every route here passes through `require(action)` first (`api/access.py`), so a
non-member is refused with 404 before the request body is read.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.access import require
from app.api.dependencies import ReviewsDep
from app.api.schemas.runs import ReviewIn, ReviewOut
from app.api.session import request_id
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(prefix="/projects/{project_id}/reviews", tags=["reviews"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_Editor = Annotated[Current, require(Action.EDIT_REVIEW)]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Ask a question in a project")
async def create_review(
    project_id: UUID,
    payload: ReviewIn,
    request: Request,
    response: Response,
    current: _Editor,
    reviews: ReviewsDep,
) -> ReviewOut:
    """The question is encrypted with the project's key before it is stored."""
    try:
        view = await reviews.create(current, project_id, payload.question, request_id(request))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response.headers["Location"] = f"{request.url.path}/{view.id}"
    return ReviewOut.from_view(view)


@router.get("", summary="The project's reviews, newest first")
async def list_reviews(
    project_id: UUID, current: _Reader, reviews: ReviewsDep
) -> list[ReviewOut]:
    return [ReviewOut.from_view(v) for v in await reviews.list_for_project(current, project_id)]


@router.get("/{review_id}", summary="One review")
async def read_review(
    project_id: UUID, review_id: UUID, current: _Reader, reviews: ReviewsDep
) -> ReviewOut:
    return ReviewOut.from_view(await reviews.get(current, project_id, review_id))
