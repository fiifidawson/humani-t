"""The search-planning conversation of one review.

Paths name a review, so a non-member gets the same 404 as a missing review before
any body is read. A prompt the leak check refused is 422 (nothing was sent); an
unusable answer or an unreachable model server is 502.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import PlansDep
from app.api.schemas.plans import (
    AcceptIn,
    AnswersIn,
    ChangesIn,
    PlanOut,
    PlanStartIn,
    PlanVersionOut,
    VocabularyUploadIn,
)
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.domain.inference import InvalidStructuredOutputError, PromptRefusedError
from app.domain.projects import Action

router = APIRouter(prefix="/reviews/{review_id}/plan", tags=["plans"])

_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"description": "The plan changed, or its review is not in a state for this"}
}


def _review_access(action: Action) -> Any:
    async def gate(review_id: UUID, current: CurrentDep, plans: PlansDep) -> Current:
        await plans.authorize_review(current, review_id, action)
        return current

    return Depends(gate)


_Reader = Annotated[Current, _review_access(Action.READ_PROJECT)]
_Editor = Annotated[Current, _review_access(Action.EDIT_REVIEW)]


@contextmanager
def _boundary() -> Iterator[None]:
    """Turn the domain's and the model's refusals into the status the interface expects."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except PromptRefusedError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This text still looks like it holds personal details after masking, so nothing "
            "was sent to the model. Remove them and try again.",
        ) from exc
    except InvalidStructuredOutputError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The model's answer could not be used. Try again.",
        ) from exc


@router.get("", summary="The review's plan: questions, versions and privacy receipts")
async def read_plan(review_id: UUID, current: _Reader, plans: PlansDep) -> PlanOut:
    return PlanOut.from_view(await plans.get(current, review_id))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Start planning: clarifying questions (deep) or a first draft (shallow)",
    responses=_CONFLICT,
)
async def start_plan(
    review_id: UUID,
    payload: PlanStartIn,
    request: Request,
    response: Response,
    current: _Editor,
    plans: PlansDep,
) -> PlanOut:
    """The question is masked before the model reads it; the receipt shows what was sent."""
    with _boundary():
        view = await plans.start(current, review_id, payload.mode, request_id(request))
    response.headers["Location"] = request.url.path
    return PlanOut.from_view(view)


@router.post(
    "/answers",
    summary="Answer or skip clarifying questions; the last one drafts the plan",
    responses=_CONFLICT,
)
async def answer_questions(
    review_id: UUID, payload: AnswersIn, request: Request, current: _Editor, plans: PlansDep
) -> PlanOut:
    answers = {a.question_id: a.answer for a in payload.answers}
    with _boundary():
        view = await plans.answer(
            current,
            review_id,
            answers,
            draft_now=payload.draft_now,
            request_id=request_id(request),
        )
    return PlanOut.from_view(view)


@router.post(
    "/changes",
    summary="Ask the model for the next version of the draft",
    responses=_CONFLICT,
)
async def request_changes(
    review_id: UUID, payload: ChangesIn, request: Request, current: _Editor, plans: PlansDep
) -> PlanOut:
    """Refused for your own terms (route B): the model never changes them."""
    with _boundary():
        view = await plans.request_changes(
            current, review_id, payload.request, payload.based_on_version, request_id(request)
        )
    return PlanOut.from_view(view)


@router.post(
    "/accept",
    summary="Accept the latest draft and freeze its vocabulary",
    responses={409: {"description": "Someone already accepted it, or it is not the latest"}},
)
async def accept_plan(
    review_id: UUID, payload: AcceptIn, request: Request, current: _Editor, plans: PlansDep
) -> PlanOut:
    """The first accept wins; a second, concurrent one gets 409 and the winner stands."""
    view = await plans.accept(current, review_id, payload.version, request_id(request))
    return PlanOut.from_view(view)


@router.put(
    "/vocabulary",
    summary="Use your own search terms, exactly as given",
    responses=_CONFLICT,
)
async def upload_vocabulary(
    review_id: UUID,
    payload: VocabularyUploadIn,
    request: Request,
    current: _Editor,
    plans: PlansDep,
) -> PlanOut:
    """Parsed by code; no model is called and nothing leaves the deployment."""
    criteria = (
        None if payload.criteria is None else [(c.kind, c.text) for c in payload.criteria]
    )
    with _boundary():
        view = await plans.upload_vocabulary(
            current, review_id, payload.content, payload.format, criteria, request_id(request)
        )
    return PlanOut.from_view(view)


@router.get(
    "/versions/{version}",
    summary="One version of the plan, and what changed from the one before",
)
async def read_version(
    review_id: UUID, version: int, current: _Reader, plans: PlansDep
) -> PlanVersionOut:
    return PlanVersionOut.from_view(await plans.version(current, review_id, version))
