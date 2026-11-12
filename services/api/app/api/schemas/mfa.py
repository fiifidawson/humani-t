"""Request and response models for second factors.

WebAuthn payloads pass through as objects; the passkey verifier checks their shape.
Responses never carry a TOTP secret after enrolment, a public key or a credential id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.mfa import MfaStatus
from app.domain.mfa import Passkey, TotpCredential


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TotpCodeIn(_Strict):
    # ASCII digits only: `\d` would also admit other scripts' digits.
    code: str = Field(pattern=r"^[0-9]{6}$")


class RecoveryCodeIn(_Strict):
    code: str = Field(min_length=1, max_length=64)


class PasskeyAssertionIn(_Strict):
    credential: dict[str, Any]


class PasskeyRegistrationIn(_Strict):
    name: str = Field(min_length=1, max_length=100)
    credential: dict[str, Any]


class WebAuthnOptionsOut(BaseModel):
    """Pass `options` to `PublicKeyCredential.parse…OptionsFromJSON` in the browser."""

    options: dict[str, Any]


class TotpEnrolmentOut(BaseModel):
    """Shown once. Put `otpauth_uri` in a QR code; `secret` is for typing in by hand."""

    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class EnrolledOut(BaseModel):
    """`recovery_codes` only for the account's first authenticator; shown once."""

    recovery_codes: list[str] | None


class PasskeyOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_domain(cls, passkey: Passkey) -> PasskeyOut:
        return cls(
            id=passkey.id,
            name=passkey.name,
            created_at=passkey.created_at,
            last_used_at=passkey.last_used_at,
        )


class PasskeyRegisteredOut(BaseModel):
    passkey: PasskeyOut
    recovery_codes: list[str] | None


class TotpOut(BaseModel):
    id: UUID
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def from_domain(cls, credential: TotpCredential) -> TotpOut:
        return cls(
            id=credential.id,
            created_at=credential.created_at,
            confirmed_at=credential.confirmed_at,
        )


class MfaStatusOut(BaseModel):
    is_enrolled: bool
    totp: TotpOut | None
    passkeys: list[PasskeyOut]
    recovery_codes_remaining: int

    @classmethod
    def from_domain(cls, status: MfaStatus) -> MfaStatusOut:
        return cls(
            is_enrolled=status.is_enrolled,
            totp=TotpOut.from_domain(status.totp) if status.totp is not None else None,
            passkeys=[PasskeyOut.from_domain(p) for p in status.passkeys],
            recovery_codes_remaining=status.recovery_codes_remaining,
        )
