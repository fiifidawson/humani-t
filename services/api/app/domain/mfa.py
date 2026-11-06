"""Multi-factor authentication: one-time codes, recovery codes, passkeys and their rules.

Pure: the standard library and other domain modules only. TOTP is small enough
to implement here (RFC 6238 over RFC 4226), which keeps the replay rule — steps
only move forward — next to the code it protects. Verifying a WebAuthn ceremony
is a provider's job (`app/auth/webauthn.py`); what is stored about a passkey,
and the sign-counter rule, live here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import quote, urlencode
from uuid import UUID

from app.domain.accounts import AccountError

_TOTP_SECRET_BYTES = 20  # RFC 4226 §4: the length of the HMAC-SHA1 output
_ASCII_DIGITS = frozenset("0123456789")

RECOVERY_CODE_COUNT = 10
# Crockford base32: no I, L, O or U, so a code copied from paper is not misread.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RECOVERY_CODE_SYMBOLS = 16  # 16 x 5 bits = 80 bits, above NIST's 64 for look-up secrets
_RECOVERY_CODE_GROUP = 4
_CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1"})

WEBAUTHN_CHALLENGE_LIFETIME = timedelta(minutes=5)
_CHALLENGE_BYTES = 32  # WebAuthn §13.4.3 asks for at least 16


def _require_aware(owner: str, **values: datetime | None) -> None:
    for name, value in values.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TotpPolicy:
    """The parameters every authenticator app assumes when it scans a QR code."""

    algorithm: str = "SHA1"
    digits: int = 6
    step: timedelta = timedelta(seconds=30)
    allowed_drift_steps: int = 1


def new_totp_secret() -> bytes:
    return secrets.token_bytes(_TOTP_SECRET_BYTES)


def totp_step(at: datetime, policy: TotpPolicy = TotpPolicy()) -> int:  # noqa: B008 — frozen
    return int(at.timestamp()) // int(policy.step.total_seconds())


def _hotp(secret: bytes, counter: int, digits: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def totp_code(secret: bytes, at: datetime, policy: TotpPolicy = TotpPolicy()) -> str:  # noqa: B008
    return _hotp(secret, totp_step(at, policy), policy.digits)


def matching_totp_step(
    secret: bytes,
    code: str,
    now: datetime,
    *,
    last_used_step: int | None,
    policy: TotpPolicy = TotpPolicy(),  # noqa: B008 — frozen dataclass, safe as a default
) -> int | None:
    """Return the time step `code` belongs to, or `None` if it is not acceptable now.

    Acceptable means: within the allowed drift of `now`, and later than the last
    step ever used, so a code — or any older one — works at most once.
    """
    if len(code) != policy.digits or not set(code) <= _ASCII_DIGITS:
        return None
    current = totp_step(now, policy)
    drift = policy.allowed_drift_steps
    for step in range(current - drift, current + drift + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(_hotp(secret, step, policy.digits), code):
            return step
    return None


def totp_provisioning_uri(
    secret: bytes,
    *,
    account_name: str,
    issuer: str = "Humani-T",
    policy: TotpPolicy = TotpPolicy(),  # noqa: B008 — frozen dataclass, safe as a default
) -> str:
    """The Key URI Format that authenticator apps read from a QR code."""
    label = quote(f"{issuer}:{account_name}", safe=":")
    query = urlencode(
        {
            "secret": base64.b32encode(secret).decode().rstrip("="),
            "issuer": issuer,
            "algorithm": policy.algorithm,
            "digits": policy.digits,
            "period": int(policy.step.total_seconds()),
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"


@dataclass(frozen=True, slots=True)
class TotpCredential:
    """A user's TOTP authenticator. The secret exists here only encrypted."""

    id: UUID
    user_id: UUID
    secret_sealed: bytes
    created_at: datetime
    confirmed_at: datetime | None = None
    last_used_step: int | None = None

    def __post_init__(self) -> None:
        _require_aware(
            "TotpCredential", created_at=self.created_at, confirmed_at=self.confirmed_at
        )
        if self.last_used_step is not None and self.last_used_step < 0:
            raise ValueError("TotpCredential.last_used_step must not be negative")

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def confirmed(self, at: datetime) -> TotpCredential:
        return replace(self, confirmed_at=at)


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> tuple[str, ...]:
    """Fresh codes, formatted `XXXX-XXXX-XXXX-XXXX` for reading off paper."""
    return tuple(_recovery_code() for _ in range(count))


def _recovery_code() -> str:
    symbols = "".join(secrets.choice(_CROCKFORD) for _ in range(_RECOVERY_CODE_SYMBOLS))
    groups = range(0, _RECOVERY_CODE_SYMBOLS, _RECOVERY_CODE_GROUP)
    return "-".join(symbols[i : i + _RECOVERY_CODE_GROUP] for i in groups)


def normalize_recovery_code(code: str) -> str:
    """Ignore case, spaces and dashes; read O as 0 and I or L as 1, as Crockford does."""
    compact = "".join(code.split()).replace("-", "").upper()
    return compact.translate(_CONFUSABLES)


def recovery_code_digest(key: bytes, code: str) -> bytes:
    # Keyed, not a plain hash: 80 bits is far beyond brute force online, but a
    # copy of the table alone must not even let anyone check a guess offline.
    return hmac.new(key, normalize_recovery_code(code).encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class RecoveryCode:
    id: UUID
    user_id: UUID
    code_hash: bytes
    created_at: datetime
    used_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware("RecoveryCode", created_at=self.created_at, used_at=self.used_at)

    @property
    def is_usable(self) -> bool:
        return self.used_at is None

    def used(self, at: datetime) -> RecoveryCode:
        return replace(self, used_at=at)


@dataclass(frozen=True, slots=True)
class Passkey:
    """A registered WebAuthn credential: its id, public key and signature counter."""

    id: UUID
    user_id: UUID
    credential_id: bytes
    public_key: bytes
    sign_count: int
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    transports: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_aware("Passkey", created_at=self.created_at, last_used_at=self.last_used_at)
        if not self.name.strip():
            raise ValueError("Passkey.name must not be blank")
        if self.sign_count < 0:
            raise ValueError("Passkey.sign_count must not be negative")

    def accepts_sign_count(self, presented: int) -> bool:
        """WebAuthn §6.1.1: a counter that fails to advance suggests a cloned key.

        Authenticators that do not count report zero every time; that is allowed.
        """
        if self.sign_count == 0 and presented == 0:
            return True
        return presented > self.sign_count


@dataclass(frozen=True, slots=True)
class VerifiedRegistration:
    """What a verified registration ceremony proved: a new key, bound to us."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerifiedAssertion:
    """What a verified sign-in ceremony proved: the key signed our challenge."""

    credential_id: bytes
    sign_count: int


class PasskeyVerificationError(Exception):
    """A WebAuthn response failed verification. The message is for logs, not users."""


class ChallengePurpose(StrEnum):
    REGISTRATION = "registration"
    AUTHENTICATION = "authentication"


def new_webauthn_challenge() -> bytes:
    return secrets.token_bytes(_CHALLENGE_BYTES)


@dataclass(frozen=True, slots=True)
class WebAuthnChallenge:
    """A challenge issued to one session for one ceremony. Spent by its first answer."""

    id: UUID
    session_id: UUID
    user_id: UUID
    purpose: ChallengePurpose
    challenge: bytes
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(
            "WebAuthnChallenge",
            created_at=self.created_at,
            expires_at=self.expires_at,
            used_at=self.used_at,
        )
        # Stored as text; coming back from storage it must still be the enum.
        object.__setattr__(self, "purpose", ChallengePurpose(self.purpose))

    def is_usable(self, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at

    def used(self, at: datetime) -> WebAuthnChallenge:
        return replace(self, used_at=at)


@dataclass(frozen=True, slots=True)
class MfaPolicy:
    """Whether every account must have a second factor before it can do anything."""

    is_required: bool = True


@dataclass(frozen=True, slots=True)
class MfaEnrolment:
    """Which authenticators an account has. Recovery codes are a fallback, not one of them."""

    has_totp: bool
    passkey_count: int

    def __post_init__(self) -> None:
        if self.passkey_count < 0:
            raise ValueError("MfaEnrolment.passkey_count must not be negative")

    @property
    def authenticator_count(self) -> int:
        return int(self.has_totp) + self.passkey_count

    @property
    def is_enrolled(self) -> bool:
        return self.authenticator_count > 0

    @property
    def can_remove_an_authenticator(self) -> bool:
        return self.authenticator_count > 1


class SecondFactorRejectedError(AccountError):
    """A code or passkey at sign-in was wrong, replayed or expired — not saying which."""


class EnrolmentCodeIncorrectError(AccountError):
    """The code that should confirm a new authenticator did not match."""


class PasskeyRegistrationRejectedError(AccountError):
    """A passkey registration failed verification: origin, challenge, signature or flags."""


class LastAuthenticatorError(AccountError):
    """Removing this authenticator would leave the account without a second factor."""


class TotpAlreadyEnrolledError(AccountError):
    """The account already has a TOTP authenticator."""


class PasskeyAlreadyRegisteredError(AccountError):
    """This credential is already registered."""


class NotEnrolledError(AccountError):
    """The action needs an authenticator, and the account has none."""


class MfaRequiredError(AccountError):
    """The session has passed the password and must complete its second factor first."""


class MfaEnrolmentRequiredError(AccountError):
    """The account must enrol an authenticator before it can do this."""
