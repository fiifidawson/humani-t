"""Collections, a project's collection and tags, pins, and bulk actions on projects.

Collections are shared labels outside any project (ADR 0016): any signed-in user lists
and creates them; their creator or an administrator renames or deletes one. Routes under
`/projects/{project_id}` pass through `require(action)` first, as every project route does.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request, Response, status

from app.api.access import require
from app.api.dependencies import CollectionsDep, ProjectsDep
from app.api.schemas.collections import (
    ArchiveProjectsIn,
    BulkActionIn,
    BulkOut,
    CollectionIn,
    CollectionOut,
    MoveProjectsIn,
    ProjectCollectionIn,
    ProjectTagsIn,
)
from app.api.schemas.projects import ProjectOut
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(tags=["collections"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_Owner = Annotated[Current, require(Action.UPDATE_PROJECT)]


async def _checked[T](change: Awaitable[T]) -> T:
    """Run a use case that takes text; the domain's refusal becomes a 422."""
    try:
        return await change
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/collections", summary="Every collection, with the caller's own counts")
async def list_collections(
    current: CurrentDep, collections: CollectionsDep
) -> list[CollectionOut]:
    return [CollectionOut.from_view(v) for v in await collections.list_collections(current)]


@router.post(
    "/collections",
    status_code=status.HTTP_201_CREATED,
    summary="Create a collection",
    responses={
        409: {"description": "Another collection uses this name (`collection_name_taken`)"}
    },
)
async def create_collection(
    payload: CollectionIn,
    request: Request,
    response: Response,
    current: CurrentDep,
    collections: CollectionsDep,
) -> CollectionOut:
    """Any signed-in user. Its name is visible to everyone in the deployment."""
    view = await _checked(collections.create(current, payload.name, request_id(request)))
    response.headers["Location"] = f"{request.url.path}/{view.id}"
    return CollectionOut.from_view(view)


@router.patch(
    "/collections/{collection_id}",
    summary="Rename a collection",
    responses={403: {"description": "Not its creator or an administrator"}},
)
async def rename_collection(
    collection_id: UUID,
    payload: CollectionIn,
    request: Request,
    current: CurrentDep,
    collections: CollectionsDep,
) -> CollectionOut:
    view = await _checked(
        collections.rename(current, collection_id, payload.name, request_id(request))
    )
    return CollectionOut.from_view(view)


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection; the projects in it stay, in no collection",
)
async def delete_collection(
    collection_id: UUID, request: Request, current: CurrentDep, collections: CollectionsDep
) -> None:
    await collections.delete(current, collection_id, request_id(request))


@router.put(
    "/projects/{project_id}/collection",
    summary="File the project in a collection, or in none",
    responses={422: {"description": "No such collection"}},
)
async def move_project(
    project_id: UUID,
    payload: ProjectCollectionIn,
    request: Request,
    current: _Owner,
    collections: CollectionsDep,
    projects: ProjectsDep,
) -> ProjectOut:
    await collections.move(current, project_id, payload.collection_id, request_id(request))
    return ProjectOut.from_view(await projects.get(current, project_id))


@router.put("/projects/{project_id}/tags", summary="Replace the project's tags")
async def set_project_tags(
    project_id: UUID,
    payload: ProjectTagsIn,
    request: Request,
    current: _Owner,
    collections: CollectionsDep,
    projects: ProjectsDep,
) -> ProjectOut:
    """Owners only. Tags are trimmed and repeats dropped, ignoring case."""
    await _checked(collections.set_tags(current, project_id, payload.tags, request_id(request)))
    return ProjectOut.from_view(await projects.get(current, project_id))


@router.put(
    "/projects/{project_id}/pin",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Pin the project for yourself",
)
async def pin_project(project_id: UUID, current: _Reader, collections: CollectionsDep) -> None:
    await collections.pin(current, project_id)


@router.delete(
    "/projects/{project_id}/pin",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unpin the project",
)
async def unpin_project(
    project_id: UUID, current: _Reader, collections: CollectionsDep
) -> None:
    await collections.unpin(current, project_id)


@router.post("/project-actions", summary="Move, tag or archive several projects at once")
async def bulk_project_action(
    payload: Annotated[BulkActionIn, Body()],
    request: Request,
    current: CurrentDep,
    collections: CollectionsDep,
) -> BulkOut:
    """Each project the caller owns is changed; every other one is listed in `skipped`.

    A project the caller is not in is skipped as `not_found`, exactly like a missing one.
    `422` for an unknown collection, before anything changes.
    """
    rid = request_id(request)
    if isinstance(payload, MoveProjectsIn):
        change = collections.move_many(current, payload.project_ids, payload.collection_id, rid)
    elif isinstance(payload, ArchiveProjectsIn):
        change = collections.archive_many(current, payload.project_ids, rid)
    else:
        change = collections.tag_many(current, payload.project_ids, payload.tag, rid)
    return BulkOut.from_outcome(await _checked(change))
