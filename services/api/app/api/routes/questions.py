"""Follow-up questions in threads.

Asking returns at once with the question masked and queued; the worker answers it
while the browser polls. A non-member gets the same 404 as a missing id, before the
body is read.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import API_PREFIX, QuestionsDep
from app.api.schemas.questions import QuestionIn, QuestionOut, ThreadOut
from app.api.session import CurrentDep, request_id
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(tags=["questions"])


def _review_access(action: Action) -> Any:
    async def gate(review_id: UUID, current: CurrentDep, questions: QuestionsDep) -> Current:
        await questions.authorize_review(current, review_id, action)
        return current

    return Depends(gate)


_Asker = Annotated[Current, _review_access(Action.ASK_QUESTIONS)]
_Reader = Annotated[Current, _review_access(Action.READ_PROJECT)]


@router.post(
    "/reviews/{review_id}/questions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a follow-up question; it is answered in the background",
    responses={409: {"description": "The thread's last question is still being answered"}},
    response_model_exclude_unset=True,
)
async def ask_question(
    review_id: UUID,
    payload: QuestionIn,
    request: Request,
    response: Response,
    current: _Asker,
    questions: QuestionsDep,
) -> QuestionOut:
    """The question is masked before it is stored or sent. Poll the `Location` given."""
    try:
        view = await questions.ask(
            current, review_id, payload.question, payload.thread_id, request_id(request)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response.headers["Location"] = f"{API_PREFIX}/questions/{view.record.id}"
    return QuestionOut.from_view(view)


@router.get(
    "/reviews/{review_id}/threads",
    summary="A review's threads, newest first",
    response_model_exclude_unset=True,
)
async def list_threads(
    review_id: UUID, current: _Reader, questions: QuestionsDep
) -> list[ThreadOut]:
    return [ThreadOut.from_view(t) for t in await questions.threads(current, review_id)]


@router.get(
    "/threads/{thread_id}",
    summary="One thread, with its questions in order",
    response_model_exclude_unset=True,
)
async def read_thread(
    thread_id: UUID, current: CurrentDep, questions: QuestionsDep
) -> ThreadOut:
    return ThreadOut.from_view(await questions.thread(current, thread_id))


@router.get(
    "/questions/{question_id}",
    summary="One question: poll it until it is answered or failed",
    response_model_exclude_unset=True,
)
async def read_question(
    question_id: UUID, current: CurrentDep, questions: QuestionsDep
) -> QuestionOut:
    return QuestionOut.from_view(await questions.question(current, question_id))
