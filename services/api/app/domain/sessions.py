"""Signed-in browsers: how long they last and what a token is.

A session token is an opaque random value. Only its SHA-256 is stored, so a copy
of the database cannot be used to sign in as anyone. Lifetimes follow NIST
SP 800-63B rev 4 at AAL2.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

_TOKEN_BYTES = 32


def new_token() -> str:
    """256 bits from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    # A plain hash, not a password hash: the token is already 256 random bits,
    # so there is nothing for a slow hash to protect against.
    return hashlib.sha256(token.encode()).digest()


def tokens_match(token: str, expected_hash: bytes) -> bool:
    return secrets.compare_digest(hash_token(token), expected_hash)


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    idle_timeout: timedelta = timedelta(hours=1)
    absolute_timeout: timedelta = timedelta(hours=24)
    reauthentication_window: timedelta = timedelta(minutes=15)
    # Long enough to find a phone, short enough that a stolen password alone
    # holds nothing useful for long.
    pending_mfa_timeout: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Session:
    id: UUID
    user_id: UUID
    token_hash: bytes
    csrf_token_hash: bytes
    created_at: datetime
    last_seen_at: datetime
    authenticated_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    # Authenticator assurance level (NIST 800-63B): 1 after a password, 2 after
    # a second factor. A pending session has passed the password and is waiting
    # for the second factor; it may reach only the MFA endpoints.
    aal: int = 1
    is_mfa_pending: bool = False

    def __post_init__(self) -> None:
        for name in ("created_at", "last_seen_at", "authenticated_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"Session.{name} must be timezone-aware")
        if self.aal not in (1, 2):
            raise ValueError("Session.aal must be 1 or 2")
        if self.is_mfa_pending and self.aal != 1:
            raise ValueError("Session.is_mfa_pending requires aal 1")

    def is_active(self, now: datetime, policy: SessionPolicy) -> bool:
        absolute = (
            policy.pending_mfa_timeout if self.is_mfa_pending else policy.absolute_timeout
        )
        return (
            self.revoked_at is None
            and now - self.last_seen_at <= policy.idle_timeout
            and now - self.created_at <= absolute
        )

    def is_recently_authenticated(self, now: datetime, policy: SessionPolicy) -> bool:
        return now - self.authenticated_at <= policy.reauthentication_window

    def touched(self, now: datetime) -> Session:
        return replace(self, last_seen_at=now)

    def revoked(self, now: datetime) -> Session:
        return replace(self, revoked_at=now)
