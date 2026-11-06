"""Runs, their live event stream, and gates (architecture §4.4).

These paths name a review, run or gate rather than a project. Each resolves
the project from the id it names and answers a non-member with the same 404 as
a missing id. Routes with a body check access in a dependency, so the refusal
comes before the body is read; the use case checks again as it writes.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import API_PREFIX, RunsDep
from app.api.schemas.run_events import RunStreamEvent
from app.api.schemas.runs import GateDecisionIn, GateResolvedOut, RunIn, RunOut, RunStartedOut
from app.api.session import CurrentDep, request_id
from app.api.sse import MEDIA_TYPE, event_stream
from app.application.accounts import Current
from app.domain.projects import Action

router = APIRouter(tags=["runs"])


def _review_access(action: Action) -> Any:
    async def gate(review_id: UUID, current: CurrentDep, runs: RunsDep) -> Current:
        await runs.authorize_review(current, review_id, action)
        return current

    return Depends(gate)


async def _gate_access(gate_id: UUID, current: CurrentDep, runs: RunsDep) -> Current:
    await runs.authorize_gate(current, gate_id)
    return current


_RunStarter = Annotated[Current, _review_access(Action.MANAGE_RUNS)]
_GateResolver = Annotated[Current, Depends(_gate_access)]


@router.post(
    "/reviews/{review_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a run of some pipeline stages",
    responses={409: {"description": "A run is already going for this review"}},
)
async def start_run(
    review_id: UUID,
    payload: RunIn,
    request: Request,
    response: Response,
    current: _RunStarter,
    runs: RunsDep,
) -> RunStartedOut:
    """Returns at once; a worker runs the stages. Follow them at the `Location` given."""
    try:
        run = await runs.start(current, review_id, payload.stages, request_id(request))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response.headers["Location"] = f"{API_PREFIX}/runs/{run.id}/events"
    return RunStartedOut(run_id=run.id)


@router.get("/reviews/{review_id}/runs", summary="A review's runs, newest first")
async def list_runs(review_id: UUID, current: CurrentDep, runs: RunsDep) -> list[RunOut]:
    return [RunOut.from_domain(run) for run in await runs.list_for_review(current, review_id)]


class _EventStreamDocs(JSONResponse):
    """Documentation only: the route returns its own `StreamingResponse`.

    FastAPI publishes a `response_model` schema only for JSON response classes,
    and files it under the class's media type, so this one says "the stream's
    items look like `RunStreamEvent`" under text/event-stream.
    """

    media_type = MEDIA_TYPE


@router.get(
    "/runs/{run_id}/events",
    summary="Follow a run as Server-Sent Events",
    response_class=_EventStreamDocs,
    response_model=RunStreamEvent,
    response_description="The run's event stream: each event's `id:`, `event:` and `data:`.",
)
async def follow_run(
    run_id: UUID,
    current: CurrentDep,
    runs: RunsDep,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[
        str | None,
        Query(description="For a new `EventSource`, which cannot set the header."),
    ] = None,
) -> StreamingResponse:
    """Replay the run's stored events after the last id seen, then follow it live.

    The id comes from the `Last-Event-ID` header, which the browser sends when
    it reconnects, or else from `?last_event_id=`; the header wins. The stream
    ends after `job.finished`, so a finished run replays and closes. It also
    ends when the session or the membership does; the browser's reconnect then
    gets 401 or 404.
    """
    resume_after = last_event_id_header if last_event_id_header is not None else last_event_id
    stream = await runs.open_stream(current, run_id, resume_after)
    return StreamingResponse(
        event_stream(stream),
        media_type=MEDIA_TYPE,
        # Tells nginx-style proxies not to hold events back in a buffer.
        headers={"X-Accel-Buffering": "no"},
    )


@router.post(
    "/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a run; finished stages are kept",
    responses={409: {"description": "The run has already finished"}},
)
async def cancel_run(
    run_id: UUID, request: Request, current: CurrentDep, runs: RunsDep
) -> RunOut:
    """A queued or waiting run ends at once; a running one at its next checkpoint."""
    return RunOut.from_domain(await runs.cancel(current, run_id, request_id(request)))


@router.post(
    "/gates/{gate_id}",
    summary="Decide a gate, so the run can continue",
    responses={409: {"description": "Someone already decided, or the run stopped waiting"}},
)
async def resolve_gate(
    gate_id: UUID,
    payload: GateDecisionIn,
    request: Request,
    current: _GateResolver,
    runs: RunsDep,
) -> GateResolvedOut:
    """The first decision wins and every open stream sees it; a later one gets 409."""
    gate = await runs.resolve_gate(
        current, gate_id, payload.decision, payload.revisions, request_id(request)
    )
    return GateResolvedOut.from_domain(gate)
