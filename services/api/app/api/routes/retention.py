"""The retention period, a project's deletion date, and Keep (UI_UX_SPEC §7.7, ADR 0020).

The period is the deployment's and any signed-in person may read it. A project's date is
for its members; a non-member gets 404. Keeping restarts the clock and is for owners.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.access import require
from app.api.dependencies import RetentionDep
from app.api.schemas.retention import ProjectRetentionOut, RetentionPolicyOut
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(tags=["retention"])

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_Keeper = Annotated[Current, require(Action.UPDATE_PROJECT)]


@router.get("/retention", summary="How long this deployment keeps a project after its activity")
async def read_policy(current: CurrentDep, retention: RetentionDep) -> RetentionPolicyOut:
    return RetentionPolicyOut.from_domain(retention.policy)


@router.get("/projects/{project_id}/retention", summary="When the project will be deleted")
async def read_project_retention(
    project_id: UUID, current: _Reader, retention: RetentionDep
) -> ProjectRetentionOut:
    return ProjectRetentionOut.from_view(await retention.view(current, project_id))


@router.post(
    "/projects/{project_id}/retention/keep",
    summary="Keep the project: restart its retention clock",
    responses={409: {"description": "The deployment sets no retention period"}},
)
async def keep_project(
    project_id: UUID, request: Request, current: _Keeper, retention: RetentionDep
) -> ProjectRetentionOut:
    view = await retention.keep(current, project_id, request_id(request))
    return ProjectRetentionOut.from_view(view)
