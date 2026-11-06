"""Request and response models for accounts, sessions and invitations.

Passwords are capped at the boundary (4096 characters) so an enormous body is
rejected before it reaches the hasher; the domain policy enforces the real
limit and reports it as a violation.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.accounts import Invitation, User
from app.domain.sessions import Session

_Password = Field(min_length=1, max_length=4096)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_Strict):
    email: EmailStr
    password: str = _Password


class ReauthenticateIn(_Strict):
    password: str = _Password


class RegisterIn(_Strict):
    token: str = Field(min_length=1, max_length=128)
    password: str = _Password
    display_name: str = Field(min_length=1, max_length=200)


class PasswordResetIn(_Strict):
    email: EmailStr


class PasswordResetConfirmIn(_Strict):
    token: str = Field(min_length=1, max_length=128)
    new_password: str = _Password


class ChangePasswordIn(_Strict):
    current_password: str = _Password
    new_password: str = _Password


class InvitationIn(_Strict):
    email: EmailStr


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    is_admin: bool

    @classmethod
    def from_domain(cls, user: User) -> UserOut:
        return cls(
            id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin
        )


class SignedInOut(BaseModel):
    user: UserOut


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    current: bool

    @classmethod
    def from_domain(cls, session: Session, current_id: UUID) -> SessionOut:
        return cls(
            id=session.id,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            user_agent=session.user_agent,
            current=session.id == current_id,
        )


class CurrentSessionOut(BaseModel):
    user: UserOut
    session: SessionOut


class InvitationOut(BaseModel):
    id: UUID
    email: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, invitation: Invitation) -> InvitationOut:
        return cls(id=invitation.id, email=invitation.email, expires_at=invitation.expires_at)


class AcceptedOut(BaseModel):
    status: str = "If an account exists for that email, a reset link has been sent."
