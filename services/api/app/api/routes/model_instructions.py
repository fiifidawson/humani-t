"""A project's instructions to the model: how to judge papers, and how to write.

Every member may read them; owners save them. A non-member gets 404. Nothing here calls
a model: the texts are added to prompts when a run or a follow-up answer uses them.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.api.access import require
from app.api.dependencies import ModelInstructionsDep
from app.api.schemas.model_instructions import (
    ModelInstructionsIn,
    ModelInstructionsOut,
    ModelInstructionsVersionOut,
)
from app.api.session import request_id
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(
    prefix="/projects/{project_id}/model-instructions", tags=["model instructions"]
)

_Reader = Annotated[Current, require(Action.READ_PROJECT)]
_Owner = Annotated[Current, require(Action.UPDATE_PROJECT)]


@router.get("", summary="The project's instructions to the model and every earlier version")
async def read_instructions(
    project_id: UUID, current: _Reader, instructions: ModelInstructionsDep
) -> ModelInstructionsOut:
    return ModelInstructionsOut.from_view(await instructions.history(current, project_id))


@router.put(
    "",
    summary="Save these instructions as the project's next version",
    responses={409: {"description": "Someone saved another version since it was read"}},
)
async def save_instructions(
    project_id: UUID,
    payload: ModelInstructionsIn,
    request: Request,
    current: _Owner,
    instructions: ModelInstructionsDep,
) -> ModelInstructionsVersionOut:
    """Refuses a term the project always masks, naming the field and never the term."""
    try:
        saved = await instructions.save(
            current,
            project_id,
            payload.to_domain(),
            payload.based_on_version,
            request_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ModelInstructionsVersionOut.from_view(saved)
