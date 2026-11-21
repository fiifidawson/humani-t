"""Evaluations, in the admin area (UI_UX_SPEC §6.15): humani-t against other systems.

Administrators only, refused with 403 before the body is read. Inside, the administrator
must also be a member of the project: a non-member gets the 404 of a missing one. Starting
returns at once; the worker answers and judges while the browser polls.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import API_PREFIX, EvaluationsDep
from app.api.schemas.evaluations import (
    EvaluationIn,
    EvaluationOut,
    EvaluationSummaryOut,
    JudgeModelsOut,
)
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.application.evaluations import require_admin

router = APIRouter(prefix="/admin/evaluations", tags=["admin"])


async def _admin(current: CurrentDep) -> Current:
    require_admin(current)
    return current


_Admin = Annotated[Current, Depends(_admin)]


@router.get("/judges", summary="The models an evaluation may be judged by")
async def judge_models(current: _Admin, evaluations: EvaluationsDep) -> JudgeModelsOut:
    """Only models the operator configured. The judge should be from another model family."""
    return JudgeModelsOut(
        writer_model=evaluations.writer_model, judge_models=list(evaluations.judge_models)
    )


@router.get("", summary="Evaluations of the projects you are a member of, newest first")
async def list_evaluations(
    current: _Admin, evaluations: EvaluationsDep
) -> list[EvaluationSummaryOut]:
    return [EvaluationSummaryOut.from_record(r) for r in await evaluations.list_for(current)]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an evaluation; it is answered and judged in the background",
    responses={404: {"description": "No such project, or you are not a member of it"}},
)
async def start_evaluation(
    payload: EvaluationIn,
    request: Request,
    response: Response,
    current: _Admin,
    evaluations: EvaluationsDep,
) -> EvaluationOut:
    """The files are masked as the project chooses before they are stored or sent."""
    try:
        view = await evaluations.start(current, payload.to_domain(), request_id(request))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response.headers["Location"] = f"{API_PREFIX}/admin/evaluations/{view.record.id}"
    return EvaluationOut.from_view(view)


@router.get("/{evaluation_id}", summary="One evaluation: poll it until it has finished")
async def read_evaluation(
    evaluation_id: UUID, current: _Admin, evaluations: EvaluationsDep
) -> EvaluationOut:
    return EvaluationOut.from_view(await evaluations.get(current, evaluation_id))
