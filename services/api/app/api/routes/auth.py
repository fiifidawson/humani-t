"""Signing in and out, registering from an invitation, and resetting a password."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import AccountsDep
from app.api.schemas.accounts import (
    AcceptedOut,
    CurrentSessionOut,
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetIn,
    ReauthenticateIn,
    RegisterIn,
    SessionOut,
    SignedInOut,
    UserOut,
)
from app.api.session import CurrentDep, clear_session_cookies, request_id, set_session_cookies

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # Behind Caddy, uvicorn's --proxy-headers sets this from X-Forwarded-For,
    # trusting only the proxy's network.
    return request.client.host if request.client else "unknown"


@router.post("/login", summary="Sign in with email and password")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    accounts: AccountsDep,
) -> SignedInOut:
    """Start a session. The same 401 for a wrong password and an unknown account."""
    signed_in = await accounts.login(
        email=payload.email,
        password=payload.password,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )
    set_session_cookies(response, signed_in)
    return SignedInOut(user=UserOut.from_domain(signed_in.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def logout(request: Request, current: CurrentDep, accounts: AccountsDep) -> Response:
    await accounts.logout(current, request_id(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response)
    return response


@router.get("/session", summary="Who is signed in")
async def current_session(current: CurrentDep) -> CurrentSessionOut:
    return CurrentSessionOut(
        user=UserOut.from_domain(current.user),
        session=SessionOut.from_domain(current.session, current.session.id),
    )


@router.post(
    "/reauthenticate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm the password before a sensitive action",
)
async def reauthenticate(
    payload: ReauthenticateIn,
    request: Request,
    current: CurrentDep,
    accounts: AccountsDep,
) -> None:
    await accounts.reauthenticate(current, payload.password, request_id(request))


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Accept an invitation")
async def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    accounts: AccountsDep,
) -> SignedInOut:
    signed_in = await accounts.register(
        token=payload.token,
        password=payload.password,
        display_name=payload.display_name,
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )
    set_session_cookies(response, signed_in)
    return SignedInOut(user=UserOut.from_domain(signed_in.user))


@router.post(
    "/password-reset",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset link",
)
async def request_password_reset(
    payload: PasswordResetIn, request: Request, accounts: AccountsDep
) -> AcceptedOut:
    """Always 202 with the same body, whether or not the account exists."""
    await accounts.request_password_reset(payload.email, request_id(request))
    return AcceptedOut()


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password from a reset link",
)
async def confirm_password_reset(
    payload: PasswordResetConfirmIn,
    request: Request,
    accounts: AccountsDep,
) -> None:
    await accounts.confirm_password_reset(
        token=payload.token, new_password=payload.new_password, request_id=request_id(request)
    )
