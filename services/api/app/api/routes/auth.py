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
from app.api.session import (
    AccountBasicsDep,
    AnySessionDep,
    clear_session_cookies,
    request_id,
    set_session_cookies,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # Settled by ClientAddressMiddleware: X-Forwarded-For counts only when it
    # comes from one of the operator's trusted_proxies.
    return request.client.host if request.client else "unknown"


@router.post(
    "/login", summary="Sign in with email and password", response_model_exclude_none=True
)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    accounts: AccountsDep,
) -> SignedInOut:
    """Check the password and start a session; `status` says what comes next.

    The same 401 for a wrong password and an unknown account. With an enrolled
    second factor the session is pending: complete it under `/auth/mfa/*`.
    """
    signed_in = await accounts.login(
        email=payload.email,
        password=payload.password,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )
    set_session_cookies(response, signed_in)
    return SignedInOut.from_domain(signed_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def logout(request: Request, current: AnySessionDep, accounts: AccountsDep) -> Response:
    await accounts.logout(current, request_id(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response)
    return response


@router.get("/session", summary="Who is signed in")
async def current_session(
    current: AccountBasicsDep, accounts: AccountsDep
) -> CurrentSessionOut:
    return CurrentSessionOut(
        mfa_enrolment_required=not accounts.is_fully_signed_in(current),
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
    current: AccountBasicsDep,
    accounts: AccountsDep,
) -> Response:
    """The session continues under new cookies; the old token stops working."""
    signed_in = await accounts.reauthenticate(current, payload.password, request_id(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookies(response, signed_in)
    return response


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Accept an invitation",
    response_model_exclude_none=True,
)
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
    return SignedInOut.from_domain(signed_in)


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
