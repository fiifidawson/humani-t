"""Accounts, invitations and password resets, and the errors their rules raise.

The error types are named for what went wrong from the user's side. The API
layer decides how each one is shown; none of them carries personal data in
its message.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

INVITATION_LIFETIME = timedelta(days=7)
PASSWORD_RESET_LIFETIME = timedelta(minutes=30)


def normalize_email(email: str) -> str:
    # Lower-cased whole: technically the local part may be case-sensitive, but
    # no mail provider in practice treats it so, and two accounts differing only
    # in case would be a gift to impersonation.
    return email.strip().lower()


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    display_name: str
    password_hash: str
    created_at: datetime
    is_admin: bool = False
    is_disabled: bool = False

    def __post_init__(self) -> None:
        if self.email != normalize_email(self.email) or "@" not in self.email:
            raise ValueError("User.email must be a normalised email address")
        if not self.display_name.strip():
            raise ValueError("User.display_name must not be blank")

    def with_password_hash(self, password_hash: str) -> User:
        return replace(self, password_hash=password_hash)

    def disabled(self) -> User:
        return replace(self, is_disabled=True)


@dataclass(frozen=True, slots=True)
class Invitation:
    id: UUID
    email: str
    token_hash: bytes
    invited_by: UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None

    def is_usable(self, now: datetime) -> bool:
        return self.accepted_at is None and now < self.expires_at

    def accepted(self, now: datetime) -> Invitation:
        return replace(self, accepted_at=now)


@dataclass(frozen=True, slots=True)
class PasswordReset:
    id: UUID
    user_id: UUID
    token_hash: bytes
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None

    def is_usable(self, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at

    def used(self, now: datetime) -> PasswordReset:
        return replace(self, used_at=now)


class AccountError(Exception):
    """Base for the outcomes below. Messages are safe to show to the user."""


class InvalidCredentialsError(AccountError):
    """Email or password is incorrect — deliberately not saying which."""


class CurrentPasswordIncorrectError(AccountError):
    """A signed-in user confirming an action gave the wrong password."""


class ThrottledError(AccountError):
    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("Too many attempts. Try again later.")
        self.retry_after = retry_after


class PasswordRejectedError(AccountError):
    def __init__(self, violations: tuple[str, ...]) -> None:
        super().__init__("The password does not meet the policy.")
        self.violations = violations


class ReauthenticationRequiredError(AccountError):
    """The action needs a sign-in within the last few minutes."""


class InvalidTokenError(AccountError):
    """The link is unknown, already used, or expired — deliberately not saying which."""


class EmailTakenError(AccountError):
    """An account or open invitation already exists for this email."""


class NotFoundError(AccountError):
    """The object does not exist, or the caller may not know that it does."""


class ForbiddenError(AccountError):
    """The caller is signed in but lacks the role."""
