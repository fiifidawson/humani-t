"""Second factors: completing a sign-in with one, and managing the account's own.

`/auth/mfa/*` accepts only a pending session, one waiting for its second factor.
`/account/mfa/*` accepts a signed-in account that may not have enrolled yet, because
enrolling is how it gets past the enrolment gate.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import MfaDep
from app.api.schemas.accounts import SignedInOut
from app.api.schemas.mfa import (
    EnrolledOut,
    MfaStatusOut,
    PasskeyAssertionIn,
    PasskeyOut,
    PasskeyRegisteredOut,
    PasskeyRegistrationIn,
    RecoveryCodeIn,
    RecoveryCodesOut,
    TotpCodeIn,
    TotpEnrolmentOut,
    WebAuthnOptionsOut,
)
from app.api.session import AccountBasicsDep, PendingDep, request_id, set_session_cookies

sign_in_router = APIRouter(prefix="/auth/mfa", tags=["auth"])
account_router = APIRouter(prefix="/account/mfa", tags=["account"])

_REAUTHENTICATE: dict[int | str, dict[str, Any]] = {
    401: {"description": "Sign-in too old; reauthenticate first"}
}
_LAST_ONE: dict[int | str, dict[str, Any]] = {
    **_REAUTHENTICATE,
    409: {"description": "It is the last second factor"},
}


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@sign_in_router.post("/totp", summary="Complete sign-in with an authenticator-app code")
async def sign_in_with_totp(
    payload: TotpCodeIn, request: Request, response: Response, pending: PendingDep, mfa: MfaDep
) -> SignedInOut:
    """401 for a wrong, reused or out-of-window code; the pending session may try again."""
    signed_in = await mfa.verify_totp(
        pending, payload.code, user_agent=_user_agent(request), request_id=request_id(request)
    )
    set_session_cookies(response, signed_in)
    return SignedInOut.from_domain(signed_in)


@sign_in_router.post("/recovery-code", summary="Complete sign-in with a recovery code")
async def sign_in_with_recovery_code(
    payload: RecoveryCodeIn,
    request: Request,
    response: Response,
    pending: PendingDep,
    mfa: MfaDep,
) -> SignedInOut:
    """The code is spent, and the account is emailed that one was used."""
    signed_in = await mfa.verify_recovery_code(
        pending, payload.code, user_agent=_user_agent(request), request_id=request_id(request)
    )
    set_session_cookies(response, signed_in)
    return SignedInOut.from_domain(signed_in)


@sign_in_router.post("/passkey/options", summary="Start a passkey sign-in")
async def passkey_sign_in_options(pending: PendingDep, mfa: MfaDep) -> WebAuthnOptionsOut:
    return WebAuthnOptionsOut(options=await mfa.passkey_sign_in_options(pending))


@sign_in_router.post("/passkey", summary="Complete sign-in with a passkey")
async def sign_in_with_passkey(
    payload: PasskeyAssertionIn,
    request: Request,
    response: Response,
    pending: PendingDep,
    mfa: MfaDep,
) -> SignedInOut:
    """The challenge from `/passkey/options` answers one attempt, whatever its outcome."""
    signed_in = await mfa.verify_passkey(
        pending,
        payload.credential,
        user_agent=_user_agent(request),
        request_id=request_id(request),
    )
    set_session_cookies(response, signed_in)
    return SignedInOut.from_domain(signed_in)


@account_router.get("", summary="Which second factors this account has")
async def mfa_status(current: AccountBasicsDep, mfa: MfaDep) -> MfaStatusOut:
    return MfaStatusOut.from_domain(await mfa.status(current))


@account_router.post(
    "/totp",
    status_code=status.HTTP_201_CREATED,
    summary="Start adding an authenticator app",
    responses=_REAUTHENTICATE,
)
async def start_totp(current: AccountBasicsDep, mfa: MfaDep) -> TotpEnrolmentOut:
    """The secret is shown here once; it is inactive until `/totp/confirm`."""
    enrolment = await mfa.start_totp(current)
    return TotpEnrolmentOut(secret=enrolment.secret, otpauth_uri=enrolment.otpauth_uri)


@account_router.post("/totp/confirm", summary="Activate the authenticator app with a code")
async def confirm_totp(
    payload: TotpCodeIn,
    request: Request,
    response: Response,
    current: AccountBasicsDep,
    mfa: MfaDep,
) -> EnrolledOut:
    """A first authenticator also returns recovery codes and moves this browser to aal 2."""
    enrolled = await mfa.confirm_totp(
        current, payload.code, user_agent=_user_agent(request), request_id=request_id(request)
    )
    if enrolled.signed_in is not None:
        set_session_cookies(response, enrolled.signed_in)
    codes = list(enrolled.recovery_codes) if enrolled.recovery_codes is not None else None
    return EnrolledOut(recovery_codes=codes)


@account_router.delete(
    "/totp",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the authenticator app",
    responses=_LAST_ONE,
)
async def remove_totp(request: Request, current: AccountBasicsDep, mfa: MfaDep) -> None:
    await mfa.remove_totp(current, request_id(request))


@account_router.post(
    "/passkeys/options", summary="Start adding a passkey", responses=_REAUTHENTICATE
)
async def passkey_registration_options(
    current: AccountBasicsDep, mfa: MfaDep
) -> WebAuthnOptionsOut:
    return WebAuthnOptionsOut(options=await mfa.passkey_registration_options(current))


@account_router.post(
    "/passkeys",
    status_code=status.HTTP_201_CREATED,
    summary="Finish adding a passkey",
    responses=_REAUTHENTICATE,
)
async def register_passkey(
    payload: PasskeyRegistrationIn,
    request: Request,
    response: Response,
    current: AccountBasicsDep,
    mfa: MfaDep,
) -> PasskeyRegisteredOut:
    added = await mfa.register_passkey(
        current,
        name=payload.name,
        credential=payload.credential,
        user_agent=_user_agent(request),
        request_id=request_id(request),
    )
    if added.enrolled.signed_in is not None:
        set_session_cookies(response, added.enrolled.signed_in)
    codes = added.enrolled.recovery_codes
    return PasskeyRegisteredOut(
        passkey=PasskeyOut.from_domain(added.passkey),
        recovery_codes=list(codes) if codes is not None else None,
    )


@account_router.delete(
    "/passkeys/{passkey_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a passkey",
    responses=_LAST_ONE,
)
async def remove_passkey(
    passkey_id: UUID, request: Request, current: AccountBasicsDep, mfa: MfaDep
) -> None:
    await mfa.remove_passkey(current, passkey_id, request_id(request))


@account_router.post(
    "/recovery-codes",
    status_code=status.HTTP_201_CREATED,
    summary="Replace the recovery codes",
    responses=_REAUTHENTICATE,
)
async def regenerate_recovery_codes(
    request: Request, current: AccountBasicsDep, mfa: MfaDep
) -> RecoveryCodesOut:
    """Every earlier code stops working. The new ones are shown here once."""
    codes = await mfa.regenerate_recovery_codes(current, request_id(request))
    return RecoveryCodesOut(recovery_codes=list(codes))
