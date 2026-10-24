"""The signed-in user's own account: sessions and password."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import AccountsDep
from app.api.schemas.accounts import ChangePasswordIn, SessionOut
from app.api.session import CurrentDep, request_id, set_session_cookies

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/sessions", summary="Where this account is signed in")
async def list_sessions(current: CurrentDep, accounts: AccountsDep) -> list[SessionOut]:
    sessions = await accounts.list_sessions(current)
    return [SessionOut.from_domain(s, current.session.id) for s in sessions]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out one device",
)
async def revoke_session(
    session_id: UUID,
    request: Request,
    current: CurrentDep,
    accounts: AccountsDep,
) -> None:
    await accounts.revoke_session(current, session_id, request_id(request))


@router.post(
    "/sessions/revoke-others",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out every other device",
)
async def revoke_other_sessions(
    request: Request, current: CurrentDep, accounts: AccountsDep
) -> None:
    await accounts.revoke_other_sessions(current, request_id(request))


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the password",
    responses={401: {"description": "Sign-in too old; reauthenticate first"}},
)
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    current: CurrentDep,
    accounts: AccountsDep,
) -> Response:
    """Every other session ends; this one continues under a fresh token."""
    signed_in = await accounts.change_password(
        current,
        current_password=payload.current_password,
        new_password=payload.new_password,
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookies(response, signed_in)
    return response
