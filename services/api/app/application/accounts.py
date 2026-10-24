"""Signing in, staying signed in, and getting back in.

Every method works inside the caller's unit of work and leaves committing to
the caller, so an account change and its audit record land together.

Raw tokens (session, CSRF, invitation, reset) exist only in return values and
outgoing email. Storage only ever sees their SHA-256.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from app.application.audit import Actor, AuditLog
from app.auth.base import PasswordHasher
from app.domain.accounts import (
    INVITATION_LIFETIME,
    PASSWORD_RESET_LIFETIME,
    CurrentPasswordIncorrectError,
    EmailTakenError,
    ForbiddenError,
    InvalidCredentialsError,
    InvalidTokenError,
    Invitation,
    NotFoundError,
    PasswordRejectedError,
    PasswordReset,
    ReauthenticationRequiredError,
    ThrottledError,
    User,
    normalize_email,
)
from app.domain.notifications import EmailDeliveryError, EmailMessage
from app.domain.passwords import PasswordPolicy, normalize_password
from app.domain.sessions import Session, SessionPolicy, hash_token, new_token, tokens_match
from app.email.base import EmailProvider
from app.ratelimit.base import RateDecision, RateLimiter
from app.storage.base import UnitOfWork

_LOGIN_WINDOW = timedelta(minutes=15)
_LOGIN_FAILURES_PER_ACCOUNT = 10
_LOGIN_ATTEMPTS_PER_CLIENT = 100
_RESETS_PER_EMAIL = 5
# Argon2 holds 64 MiB per hash; this caps memory under a burst of sign-ins.
_CONCURRENT_HASHES = 4

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A new session and the two raw tokens the browser must be given."""

    user: User
    session: Session
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class Current:
    """The signed-in caller of a request."""

    user: User
    session: Session

    def actor(self, request_id: str | None = None) -> Actor:
        return Actor(self.user.id, request_id)


class AccountService:
    def __init__(
        self,
        *,
        hasher: PasswordHasher,
        email: EmailProvider,
        limiter: RateLimiter,
        audit: AuditLog,
        public_origin: str,
        password_policy: PasswordPolicy | None = None,
        session_policy: SessionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._hasher = hasher
        self._email = email
        self._limiter = limiter
        self._audit = audit
        self._origin = public_origin.rstrip("/")
        self._passwords = password_policy or PasswordPolicy()
        self.session_policy = session_policy or SessionPolicy()
        self._clock = clock
        self._hash_slots = asyncio.Semaphore(_CONCURRENT_HASHES)
        # Verified against when the account does not exist, so the unknown-account
        # path costs the same as the wrong-password path.
        self._dummy_hash = hasher.hash(new_token())

    async def create_user(
        self,
        uow: UnitOfWork,
        *,
        email: str,
        password: str,
        display_name: str,
        is_admin: bool = False,
    ) -> User:
        email = normalize_email(email)
        if await uow.users.get_by_email(email) is not None:
            raise EmailTakenError("An account with this email already exists.")
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=display_name.strip(),
            password_hash=await self._new_hash(password, context=(email, display_name)),
            created_at=self._clock(),
            is_admin=is_admin,
        )
        await uow.users.add(user)
        return user

    async def disable_user(self, uow: UnitOfWork, admin: Current, user_id: UUID) -> None:
        self._require_admin(admin)
        user = await uow.users.get(user_id)
        if user is None:
            raise NotFoundError("No such user.")
        await uow.users.save(user.disabled())
        await self._revoke_all(uow, user.id)
        await self._audit.record(uow, admin.actor(), "account.disabled", "user", str(user.id))

    async def login(
        self,
        uow: UnitOfWork,
        *,
        email: str,
        password: str,
        client_ip: str,
        user_agent: str | None,
        request_id: str | None = None,
    ) -> SignedIn:
        email = normalize_email(email)
        account_key = f"login:account:{email}"
        # Checked before the password, so a correct guess made while throttled
        # gets the same answer as a wrong one.
        self._enforce(
            await self._limiter.check(account_key, _LOGIN_FAILURES_PER_ACCOUNT, _LOGIN_WINDOW)
        )
        self._enforce(
            await self._limiter.hit(
                f"login:client:{client_ip}", _LOGIN_ATTEMPTS_PER_CLIENT, _LOGIN_WINDOW
            )
        )

        user = await uow.users.get_by_email(email)
        stored = user.password_hash if user is not None else self._dummy_hash
        password_ok = await self._verify(stored, password)
        if user is None or not password_ok or user.is_disabled:
            await self._limiter.hit(account_key, _LOGIN_FAILURES_PER_ACCOUNT, _LOGIN_WINDOW)
            await self._audit.record(
                uow,
                Actor(None, request_id),
                "auth.login_failed",
                "account",
                str(user.id) if user is not None else "unknown",
            )
            raise InvalidCredentialsError("Email or password is incorrect.")

        await self._limiter.reset(account_key)
        if self._hasher.needs_rehash(user.password_hash):
            user = user.with_password_hash(await self._hash(password))
            await uow.users.save(user)
        signed_in = await self._start_session(uow, user, user_agent)
        await self._audit.record(
            uow, Actor(user.id, request_id), "auth.login", "session", str(signed_in.session.id)
        )
        return signed_in

    async def authenticate(self, uow: UnitOfWork, session_token: str) -> Current | None:
        """Resolve a session cookie to its caller, extending the idle window.

        `None` covers every way a token can be unusable — unknown, revoked,
        expired, or belonging to a disabled account — so callers cannot leak which.
        """
        now = self._clock()
        session = await uow.sessions.get_by_token_hash(hash_token(session_token))
        if session is None or not session.is_active(now, self.session_policy):
            return None
        user = await uow.users.get(session.user_id)
        if user is None or user.is_disabled:
            return None
        session = session.touched(now)
        await uow.sessions.save(session)
        return Current(user, session)

    @staticmethod
    def csrf_token_matches(current: Current, token: str) -> bool:
        return bool(token) and tokens_match(token, current.session.csrf_token_hash)

    async def logout(self, uow: UnitOfWork, current: Current, request_id: str | None) -> None:
        await uow.sessions.save(current.session.revoked(self._clock()))
        await self._audit.record(
            uow, current.actor(request_id), "auth.logout", "session", str(current.session.id)
        )

    async def reauthenticate(
        self, uow: UnitOfWork, current: Current, password: str, request_id: str | None
    ) -> None:
        if not await self._verify(current.user.password_hash, password):
            raise CurrentPasswordIncorrectError("The password is incorrect.")
        await uow.sessions.save(current.session.reauthenticated(self._clock()))
        await self._audit.record(
            uow,
            current.actor(request_id),
            "auth.reauthenticated",
            "session",
            str(current.session.id),
        )

    async def list_sessions(self, uow: UnitOfWork, current: Current) -> list[Session]:
        now = self._clock()
        return [
            s
            for s in await uow.sessions.list_unrevoked_for_user(current.user.id)
            if s.is_active(now, self.session_policy)
        ]

    async def revoke_session(
        self, uow: UnitOfWork, current: Current, session_id: UUID, request_id: str | None
    ) -> None:
        session = await uow.sessions.get(session_id)
        # Another user's session is "not found", never "forbidden": its existence
        # is not this caller's business.
        if session is None or session.user_id != current.user.id or session.revoked_at:
            raise NotFoundError("No such session.")
        await uow.sessions.save(session.revoked(self._clock()))
        await self._audit.record(
            uow, current.actor(request_id), "auth.session_revoked", "session", str(session.id)
        )

    async def revoke_other_sessions(
        self, uow: UnitOfWork, current: Current, request_id: str | None
    ) -> None:
        await self._revoke_all(uow, current.user.id, keep=current.session.id)
        await self._audit.record(
            uow,
            current.actor(request_id),
            "auth.other_sessions_revoked",
            "user",
            str(current.user.id),
        )

    async def change_password(
        self,
        uow: UnitOfWork,
        current: Current,
        *,
        current_password: str,
        new_password: str,
        user_agent: str | None,
        request_id: str | None,
    ) -> SignedIn:
        """Change the password, end every other session, and rotate this one."""
        if not current.session.is_recently_authenticated(self._clock(), self.session_policy):
            raise ReauthenticationRequiredError("Confirm your password to continue.")
        if not await self._verify(current.user.password_hash, current_password):
            raise CurrentPasswordIncorrectError("The current password is incorrect.")
        user = current.user.with_password_hash(
            await self._new_hash(
                new_password, context=(current.user.email, current.user.display_name)
            )
        )
        await uow.users.save(user)
        await self._revoke_all(uow, user.id)
        signed_in = await self._start_session(uow, user, user_agent)
        await self._audit.record(
            uow, current.actor(request_id), "account.password_changed", "user", str(user.id)
        )
        await self._notify_password_changed(user)
        return signed_in

    async def request_password_reset(
        self, uow: UnitOfWork, email: str, request_id: str | None
    ) -> None:
        """Send a reset link if the account exists. Callers answer the same either way."""
        email = normalize_email(email)
        self._enforce(
            await self._limiter.hit(f"reset:email:{email}", _RESETS_PER_EMAIL, _LOGIN_WINDOW)
        )
        user = await uow.users.get_by_email(email)
        await self._audit.record(
            uow,
            Actor(None, request_id),
            "auth.password_reset_requested",
            "account",
            str(user.id) if user is not None else "unknown",
        )
        if user is None or user.is_disabled:
            return

        now = self._clock()
        for older in await uow.password_resets.list_unused_for_user(user.id):
            await uow.password_resets.save(older.used(now))
        token = new_token()
        await uow.password_resets.add(
            PasswordReset(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_token(token),
                created_at=now,
                expires_at=now + PASSWORD_RESET_LIFETIME,
            )
        )
        await self._send_quietly(
            EmailMessage(
                to=user.email,
                subject="Reset your humani-t password",
                body=(
                    "Someone asked to reset the password for your humani-t account.\n\n"
                    f"{self._origin}/reset-password#token={token}\n\n"
                    "The link works once and expires in 30 minutes. If it was not you, "
                    "ignore this email; your password has not changed.\n"
                ),
            )
        )

    async def confirm_password_reset(
        self, uow: UnitOfWork, token: str, new_password: str, request_id: str | None
    ) -> None:
        now = self._clock()
        reset = await uow.password_resets.get_by_token_hash(hash_token(token))
        if reset is None or not reset.is_usable(now):
            raise InvalidTokenError("This link is invalid or has expired.")
        user = await uow.users.get(reset.user_id)
        if user is None or user.is_disabled:
            raise InvalidTokenError("This link is invalid or has expired.")

        user = user.with_password_hash(
            await self._new_hash(new_password, context=(user.email, user.display_name))
        )
        await uow.users.save(user)
        await uow.password_resets.save(reset.used(now))
        await self._revoke_all(uow, user.id)
        await self._audit.record(
            uow,
            Actor(user.id, request_id),
            "auth.password_reset_completed",
            "user",
            str(user.id),
        )
        await self._notify_password_changed(user)

    async def invite(
        self, uow: UnitOfWork, admin: Current, email: str, request_id: str | None
    ) -> Invitation:
        self._require_admin(admin)
        email = normalize_email(email)
        if await uow.users.get_by_email(email) is not None:
            raise EmailTakenError("An account with this email already exists.")
        now = self._clock()
        token = new_token()
        invitation = Invitation(
            id=uuid.uuid4(),
            email=email,
            token_hash=hash_token(token),
            invited_by=admin.user.id,
            created_at=now,
            expires_at=now + INVITATION_LIFETIME,
        )
        await uow.invitations.add(invitation)
        await self._audit.record(
            uow, admin.actor(request_id), "account.invited", "invitation", str(invitation.id)
        )
        await self._email.send(
            EmailMessage(
                to=email,
                subject="You have been invited to humani-t",
                body=(
                    f"{admin.user.display_name} invited you to humani-t.\n\n"
                    f"{self._origin}/accept-invitation#token={token}\n\n"
                    "The link works once and expires in 7 days.\n"
                ),
            )
        )
        return invitation

    async def register(
        self,
        uow: UnitOfWork,
        *,
        token: str,
        password: str,
        display_name: str,
        user_agent: str | None,
        request_id: str | None,
    ) -> SignedIn:
        now = self._clock()
        invitation = await uow.invitations.get_by_token_hash(hash_token(token))
        if invitation is None or not invitation.is_usable(now):
            raise InvalidTokenError("This invitation is invalid or has expired.")
        user = await self.create_user(
            uow, email=invitation.email, password=password, display_name=display_name
        )
        await uow.invitations.save(invitation.accepted(now))
        signed_in = await self._start_session(uow, user, user_agent)
        await self._audit.record(
            uow, Actor(user.id, request_id), "account.registered", "user", str(user.id)
        )
        return signed_in

    async def _start_session(
        self, uow: UnitOfWork, user: User, user_agent: str | None
    ) -> SignedIn:
        now = self._clock()
        session_token, csrf_token = new_token(), new_token()
        session = Session(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            created_at=now,
            last_seen_at=now,
            authenticated_at=now,
            user_agent=user_agent[:256] if user_agent else None,
        )
        await uow.sessions.add(session)
        return SignedIn(user, session, session_token, csrf_token)

    async def _revoke_all(
        self, uow: UnitOfWork, user_id: UUID, keep: UUID | None = None
    ) -> None:
        now = self._clock()
        for session in await uow.sessions.list_unrevoked_for_user(user_id):
            if session.id != keep:
                await uow.sessions.save(session.revoked(now))

    async def _new_hash(self, password: str, context: tuple[str, ...]) -> str:
        violations = self._passwords.violations(password, context)
        if violations:
            raise PasswordRejectedError(violations)
        return await self._hash(password)

    async def _hash(self, password: str) -> str:
        async with self._hash_slots:
            return await asyncio.to_thread(self._hasher.hash, normalize_password(password))

    async def _verify(self, stored_hash: str, password: str) -> bool:
        async with self._hash_slots:
            return await asyncio.to_thread(
                self._hasher.verify, stored_hash, normalize_password(password)
            )

    async def _notify_password_changed(self, user: User) -> None:
        await self._send_quietly(
            EmailMessage(
                to=user.email,
                subject="Your humani-t password was changed",
                body=(
                    "The password for your humani-t account was just changed, and every "
                    "other signed-in device was signed out.\n\nIf this was not you, reset "
                    "your password now and tell your administrator.\n"
                ),
            )
        )

    async def _send_quietly(self, message: EmailMessage) -> None:
        # A mail outage must not turn "if the account exists" into an observable
        # difference, nor undo a password change that already happened.
        try:
            await self._email.send(message)
        except EmailDeliveryError:
            _log.warning("email_delivery_failed", subject=message.subject, exc_info=True)

    @staticmethod
    def _require_admin(current: Current) -> None:
        if not current.user.is_admin:
            raise ForbiddenError("Administrators only.")

    @staticmethod
    def _enforce(decision: RateDecision) -> None:
        if not decision.allowed:
            raise ThrottledError(decision.retry_after)
