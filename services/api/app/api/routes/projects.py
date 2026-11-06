"""Projects and their members.

Every route below `/projects/{project_id}` passes through `require(action)`
first (`api/access.py`), which names the action the route needs. A route added
here without it is caught by `tests/test_route_inventory.py`.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.access import require
from app.api.dependencies import ProjectsDep
from app.api.schemas.projects import (
    MemberIn,
    MemberOut,
    MemberRoleIn,
    ProjectIn,
    ProjectOut,
    ProjectRenameIn,
)
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.application.projects import ProjectView
from app.domain.projects import Action

router = APIRouter(prefix="/projects", tags=["projects"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_Updater = Annotated[Current, require(Action.UPDATE_PROJECT)]
_Deleter = Annotated[Current, require(Action.DELETE_PROJECT, recent_sign_in=True)]
_MemberManager = Annotated[Current, require(Action.MANAGE_MEMBERS, recent_sign_in=True)]
# Removing someone else needs MANAGE_MEMBERS and a recent sign-in; leaving needs
# neither. The use case decides which applies; the gate admits any member.
_Leaver = Annotated[Current, require(Action.LEAVE_PROJECT)]


async def _named(change: Awaitable[ProjectView]) -> ProjectOut:
    """Run a use case that takes a name; the domain's refusal becomes a 422."""
    try:
        return ProjectOut.from_view(await change)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a project")
async def create_project(
    payload: ProjectIn,
    request: Request,
    response: Response,
    current: CurrentDep,
    projects: ProjectsDep,
) -> ProjectOut:
    """The caller becomes its owner, and the project gets its own encryption key."""
    created = await _named(projects.create(current, payload.name, request_id(request)))
    response.headers["Location"] = f"{request.url.path}/{created.id}"
    return created


@router.get("", summary="Projects the caller belongs to, newest first")
async def list_projects(
    current: CurrentDep, projects: ProjectsDep, include_archived: bool = False
) -> list[ProjectOut]:
    views = await projects.list_mine(current, include_archived=include_archived)
    return [ProjectOut.from_view(view) for view in views]


@router.get("/{project_id}", summary="One project")
async def read_project(project_id: UUID, current: _Reader, projects: ProjectsDep) -> ProjectOut:
    return ProjectOut.from_view(await projects.get(current, project_id))


@router.patch("/{project_id}", summary="Rename a project")
async def rename_project(
    project_id: UUID,
    payload: ProjectRenameIn,
    request: Request,
    current: _Updater,
    projects: ProjectsDep,
) -> ProjectOut:
    return await _named(projects.rename(current, project_id, payload.name, request_id(request)))


@router.post("/{project_id}/archive", summary="Hide a project from the default list")
async def archive_project(
    project_id: UUID, request: Request, current: _Updater, projects: ProjectsDep
) -> ProjectOut:
    """Keeps everything and can be undone. Archiving twice keeps the first date."""
    view = await projects.archive(current, project_id, request_id(request))
    return ProjectOut.from_view(view)


@router.post("/{project_id}/unarchive", summary="Bring an archived project back")
async def unarchive_project(
    project_id: UUID, request: Request, current: _Updater, projects: ProjectsDep
) -> ProjectOut:
    view = await projects.unarchive(current, project_id, request_id(request))
    return ProjectOut.from_view(view)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project by destroying its key",
    responses={401: {"description": "Sign-in too old; reauthenticate first"}},
)
async def delete_project(
    project_id: UUID, request: Request, current: _Deleter, projects: ProjectsDep
) -> None:
    """Irreversible: every copy of the project's data becomes unreadable.

    Copies held in backups follow once the backup retention period has passed.
    """
    await projects.delete(current, project_id, request_id(request))


@router.get("/{project_id}/members", summary="Who belongs to a project")
async def list_members(
    project_id: UUID, current: _Reader, projects: ProjectsDep
) -> list[MemberOut]:
    return [MemberOut.from_view(m) for m in await projects.list_members(current, project_id)]


@router.post(
    "/{project_id}/members",
    status_code=status.HTTP_201_CREATED,
    summary="Give an existing account a role in the project",
)
async def add_member(
    project_id: UUID,
    payload: MemberIn,
    request: Request,
    current: _MemberManager,
    projects: ProjectsDep,
) -> MemberOut:
    """`422` when no active account uses the email, without saying which."""
    view = await projects.add_member(
        current,
        project_id,
        email=str(payload.email),
        role=payload.role,
        request_id=request_id(request),
    )
    return MemberOut.from_view(view)


@router.patch("/{project_id}/members/{user_id}", summary="Change a member's role")
async def change_member_role(
    project_id: UUID,
    user_id: UUID,
    payload: MemberRoleIn,
    request: Request,
    current: _MemberManager,
    projects: ProjectsDep,
) -> MemberOut:
    """`409` with `last_owner` when it would leave the project without an owner."""
    view = await projects.change_role(
        current, project_id, user_id, payload.role, request_id(request)
    )
    return MemberOut.from_view(view)


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member, or leave",
)
async def remove_member(
    project_id: UUID,
    user_id: UUID,
    request: Request,
    current: _Leaver,
    projects: ProjectsDep,
) -> None:
    """Removing someone else is for owners; anyone may remove themselves.

    The last owner can do neither (`409` with `last_owner`).
    """
    await projects.remove_member(current, project_id, user_id, request_id(request))
