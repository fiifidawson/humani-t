"""Signing in, staying signed in, and getting back in.

Each public method is one use case and owns its transactions (the Unit of Work
pattern). Every method follows the same shape, because the alternatives fail
under load:

1. a short transaction to read what is needed;
2. slow work — password hashing — with no database connection held;
3. a short transaction to write, with race-safe operations (conditional claims,
   version-checked saves) and the audit event last, so the audit chain's lock
   is held for as little time as possible;
4. side effects — email — only after the commit.

Raw tokens (session, CSRF, invitation, reset) exist only in return values and
outgoing email. Storage only ever sees their SHA-256.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import structlog

from app.application.audit import SYSTEM, Actor, AuditLog
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
    OwnAccountError,
    PasswordRejectedError,
    PasswordReset,
    ReauthenticationRequiredError,
    ThrottledError,
    User,
    normalize_email,
    require_an_admin_remains,
)
from app.domain.errors import ConcurrentUpdateError, RateLimiterUnavailableError
from app.domain.mfa import MfaPolicy
from app.domain.notifications import EmailDeliveryError, EmailMessage
from app.domain.passwords import PasswordPolicy, normalize_password
from app.domain.sessions import Session, SessionPolicy, hash_token, new_token, tokens_match
from app.email.base import EmailProvider
from app.ratelimit.base import RateDecision, RateLimiter
from app.storage.base import StorageProvider, UnitOfWork

_LOGIN_WINDOW = timedelta(minutes=15)
_LOGIN_ATTEMPTS_PER_ACCOUNT = 10
_LOGIN_ATTEMPTS_PER_CLIENT = 100
_RESETS_PER_EMAIL = 5
# Argon2 holds 64 MiB per hash; this caps memory under a burst of sign-ins.
_CONCURRENT_HASHES = 4

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SignInStatus(StrEnum):
    """Where a sign-in stands once the password has been accepted."""

    SIGNED_IN = "signed_in"
    MFA_REQUIRED = "mfa_required"
    MFA_ENROLMENT_REQUIRED = "mfa_enrolment_required"


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A new session and the two raw tokens the browser must be given.

    `methods` lists the second factors the account can use, and is set only
    when `status` is `MFA_REQUIRED`.
    """

    user: User
    session: Session
    session_token: str
    csrf_token: str
    status: SignInStatus = SignInStatus.SIGNED_IN
    methods: tuple[str, ...] = ()


def new_session(
    user: User,
    user_agent: str | None,
    now: datetime,
    *,
    aal: int = 1,
    is_mfa_pending: bool = False,
    status: SignInStatus = SignInStatus.SIGNED_IN,
    methods: tuple[str, ...] = (),
) -> SignedIn:
    """A fresh session with fresh tokens. Never reuses a token: no fixation."""
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
        aal=aal,
        is_mfa_pending=is_mfa_pending,
    )
    return SignedIn(user, session, session_token, csrf_token, status, methods)


async def second_factor_methods(uow: UnitOfWork, user_id: UUID) -> tuple[str, ...]:
    """The second factors an account can sign in with; empty if it has none enrolled."""
    totp = await uow.totp_credentials.get_for_user(user_id)
    has_totp = totp is not None and totp.is_confirmed
    has_passkeys = bool(await uow.passkeys.list_for_user(user_id))
    if not (has_totp or has_passkeys):
        return ()
    methods = ["passkey"] if has_passkeys else []
    if has_totp:
        methods.append("totp")
    if await uow.recovery_codes.count_unused(user_id):
        methods.append("recovery_code")
    return tuple(methods)


@dataclass(frozen=True, slots=True)
class Current:
    """The signed-in caller of a request."""

    user: User
    session: Session

    def actor(self, request_id: str | None = None) -> Actor:
        return Actor(self.user.id, request_id)


def _rotated(current: Current, now: datetime) -> SignedIn:
    """The same sign-in under fresh tokens, confirmed again at `now`.

    The level and the creation time carry over: a password alone neither raises
    the level nor restarts the absolute lifetime, which only a full sign-in does.
    """
    session_token, csrf_token = new_token(), new_token()
    session = replace(
        current.session,
        id=uuid.uuid4(),
        token_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
        last_seen_at=now,
        authenticated_at=now,
    )
    return SignedIn(current.user, session, session_token, csrf_token)


@dataclass(frozen=True, slots=True)
class PurgeReport:
    sessions: int
    password_resets: int
    invitations: int
    webauthn_challenges: int = 0


class AccountService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        hasher: PasswordHasher,
        email: EmailProvider,
        limiter: RateLimiter,
        audit: AuditLog,
        public_origin: str,
        password_policy: PasswordPolicy | None = None,
        session_policy: SessionPolicy | None = None,
        mfa_policy: MfaPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._hasher = hasher
        self._email = email
        self._limiter = limiter
        self._audit = audit
        self._origin = public_origin.rstrip("/")
        self._passwords = password_policy or PasswordPolicy()
        self.session_policy = session_policy or SessionPolicy()
        self.mfa_policy = mfa_policy or MfaPolicy()
        self._clock = clock
        self._hash_slots = asyncio.Semaphore(_CONCURRENT_HASHES)
        # Verified against when the account does not exist, so the unknown-account
        # path costs the same as the wrong-password path.
        self._dummy_hash = hasher.hash(new_token())

    def with_storage(self, storage: StorageProvider) -> AccountService:
        """The same service over different storage — for tests that simulate outages."""
        clone = AccountService.__new__(AccountService)
        clone.__dict__.update(self.__dict__)
        clone._storage = storage
        return clone

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        is_admin: bool = False,
        actor: Actor = SYSTEM,
    ) -> User:
        email = normalize_email(email)
        password_hash = await self._new_hash(password, context=(email, display_name))
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=display_name.strip(),
            password_hash=password_hash,
            created_at=self._clock(),
            is_admin=is_admin,
        )
        async with self._storage.unit_of_work() as uow:
            await uow.users.add(user)
            await self._audit.record(uow, actor, "account.created", "user", str(user.id))
            await uow.commit()
        return user

    async def disable_user(
        self, admin: Current, user_id: UUID, request_id: str | None = None
    ) -> None:
        """Disable an account and end its sessions; never one's own, nor the last admin."""
        self._require_admin(admin)
        if user_id == admin.user.id:
            raise OwnAccountError("You cannot disable your own account.")
        async with self._storage.unit_of_work() as uow:
            # Taken before anything is read, so of two admins disabling each
            # other at once, the second sees the first's change and is refused.
            enabled_admins = {a.id for a in await uow.users.lock_enabled_admins()}
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("No such user.")
            require_an_admin_remains(enabled_admins, user.id)
            if admin.user.id not in enabled_admins:
                # Disabled or demoted since this request was authenticated.
                raise ForbiddenError("Administrators only.")
            await uow.users.save(user.disabled())
            await uow.sessions.revoke_all_for_user(user.id, self._clock())
            await self._audit.record(
                uow, admin.actor(request_id), "account.disabled", "user", str(user.id)
            )
            await uow.commit()

    async def enable_user(
        self, admin: Current, user_id: UUID, request_id: str | None = None
    ) -> None:
        """Let a disabled account sign in again. The sessions disabling ended stay ended.

        Unlike disabling, this needs a recent sign-in: it gives access back, and
        disabling must stay quick to reach during an incident.
        """
        self._require_admin(admin)
        if not admin.session.is_recently_authenticated(self._clock(), self.session_policy):
            raise ReauthenticationRequiredError("Confirm your password to continue.")
        async with self._storage.unit_of_work() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("No such user.")
            if not user.is_disabled:
                return
            await uow.users.save(user.enabled())
            await self._audit.record(
                uow, admin.actor(request_id), "account.enabled", "user", str(user.id)
            )
            await uow.commit()

    async def login(
        self,
        *,
        email: str,
        password: str,
        client_ip: str,
        user_agent: str | None,
        request_id: str | None = None,
    ) -> SignedIn:
        email = normalize_email(email)
        account_key = f"login:account:{email}"
        # Each attempt is counted atomically *before* the slow password check, so
        # parallel guesses cannot all slip in under the limit. A success resets
        # the count, so the real user is never held back by their own sign-ins.
        self._enforce(
            await self._limiter.hit(
                f"login:client:{client_ip}", _LOGIN_ATTEMPTS_PER_CLIENT, _LOGIN_WINDOW
            )
        )
        self._enforce(
            await self._limiter.hit(account_key, _LOGIN_ATTEMPTS_PER_ACCOUNT, _LOGIN_WINDOW)
        )

        async with self._storage.unit_of_work() as uow:
            user = await uow.users.get_by_email(email)

        stored = user.password_hash if user is not None else self._dummy_hash
        # Verified unconditionally and *before* the other checks: `or` would
        # short-circuit past it for unknown accounts, making them measurably faster.
        password_ok = await self._verify(stored, password)
        if user is None or user.is_disabled or not password_ok:
            await self._record_failed_login(user, request_id)
            raise InvalidCredentialsError("Email or password is incorrect.")

        upgraded = (
            await self._hash(password)
            if self._hasher.needs_rehash(user.password_hash)
            else None
        )
        async with self._storage.unit_of_work() as uow:
            fresh = await uow.users.get(user.id)
            # Re-checked: the password may have changed, or the account been
            # disabled, while the hash was being verified without a lock.
            if fresh is None or fresh.is_disabled or fresh.password_hash != stored:
                raise InvalidCredentialsError("Email or password is incorrect.")
            if upgraded is not None:
                await self._save_if_unchanged(uow, fresh.with_password_hash(upgraded))
            methods = await second_factor_methods(uow, fresh.id)
            # An enrolled account always gets a pending session, whatever the
            # policy says: a second factor someone set up is never skipped.
            if methods:
                signed_in = new_session(
                    fresh,
                    user_agent,
                    self._clock(),
                    is_mfa_pending=True,
                    status=SignInStatus.MFA_REQUIRED,
                    methods=methods,
                )
                action = "auth.login_pending_mfa"
            else:
                signed_in = new_session(
                    fresh, user_agent, self._clock(), status=self._unenrolled_status()
                )
                action = "auth.login"
            await uow.sessions.add(signed_in.session)
            await self._audit.record(
                uow, Actor(fresh.id, request_id), action, "session", str(signed_in.session.id)
            )
            await uow.commit()
        await self._reset_quietly(account_key)
        return signed_in

    def is_fully_signed_in(self, current: Current) -> bool:
        """Past every factor the policy demands: aal 2, or aal 1 when MFA is optional."""
        if current.session.is_mfa_pending:
            return False
        return current.session.aal == 2 or not self.mfa_policy.is_required

    def _unenrolled_status(self) -> SignInStatus:
        if self.mfa_policy.is_required:
            return SignInStatus.MFA_ENROLMENT_REQUIRED
        return SignInStatus.SIGNED_IN

    async def _reset_quietly(self, key: str) -> None:
        # The sign-in already happened; failing it now over a counter that could
        # not be cleared would only punish the user. The count expires anyway.
        try:
            await self._limiter.reset(key)
        except RateLimiterUnavailableError:
            _log.warning("rate_limit_reset_failed", exc_info=True)

    async def authenticate(self, session_token: str) -> Current | None:
        """Resolve a session cookie to its caller, extending the idle window.

        `None` covers every way a token can be unusable — unknown, revoked,
        expired, or belonging to a disabled account — so callers cannot leak which.
        """
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            session = await uow.sessions.get_by_token_hash(hash_token(session_token))
            if session is None or not session.is_active(now, self.session_policy):
                return None
            user = await uow.users.get(session.user_id)
            if user is None or user.is_disabled:
                return None
            await uow.sessions.touch(session.id, now)
            await uow.commit()
        return Current(user, session.touched(now))

    @staticmethod
    def csrf_token_matches(current: Current, token: str) -> bool:
        return bool(token) and tokens_match(token, current.session.csrf_token_hash)

    async def logout(self, current: Current, request_id: str | None) -> None:
        async with self._storage.unit_of_work() as uow:
            await uow.sessions.revoke(current.session.id, self._clock())
            await self._audit.record(
                uow,
                current.actor(request_id),
                "auth.logout",
                "session",
                str(current.session.id),
            )
            await uow.commit()

    async def reauthenticate(
        self, current: Current, password: str, request_id: str | None
    ) -> SignedIn:
        """Confirm the password and move this sign-in to new tokens (step-up rotation)."""
        if not await self._verify(current.user.password_hash, password):
            async with self._storage.unit_of_work() as uow:
                await self._audit.record(
                    uow,
                    current.actor(request_id),
                    "auth.reauthentication_failed",
                    "session",
                    str(current.session.id),
                )
                await uow.commit()
            raise CurrentPasswordIncorrectError("The password is incorrect.")
        now = self._clock()
        rotated = _rotated(current, now)
        async with self._storage.unit_of_work() as uow:
            if not await uow.sessions.revoke(current.session.id, now):
                raise ReauthenticationRequiredError("Sign in again to continue.")
            await uow.sessions.add(rotated.session)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "auth.reauthenticated",
                "session",
                str(rotated.session.id),
            )
            await uow.commit()
        return rotated

    async def list_sessions(self, current: Current) -> list[Session]:
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            sessions = await uow.sessions.list_unrevoked_for_user(current.user.id)
        return [s for s in sessions if s.is_active(now, self.session_policy)]

    async def revoke_session(
        self, current: Current, session_id: UUID, request_id: str | None
    ) -> None:
        async with self._storage.unit_of_work() as uow:
            session = await uow.sessions.get(session_id)
            # Another user's session is "not found", never "forbidden": its
            # existence is not this caller's business.
            if session is None or session.user_id != current.user.id:
                raise NotFoundError("No such session.")
            if not await uow.sessions.revoke(session_id, self._clock()):
                raise NotFoundError("No such session.")
            await self._audit.record(
                uow,
                current.actor(request_id),
                "auth.session_revoked",
                "session",
                str(session_id),
            )
            await uow.commit()

    async def revoke_other_sessions(self, current: Current, request_id: str | None) -> None:
        async with self._storage.unit_of_work() as uow:
            await uow.sessions.revoke_all_for_user(
                current.user.id, self._clock(), keep=current.session.id
            )
            await self._audit.record(
                uow,
                current.actor(request_id),
                "auth.other_sessions_revoked",
                "user",
                str(current.user.id),
            )
            await uow.commit()

    async def change_password(
        self,
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
        new_hash = await self._new_hash(
            new_password, context=(current.user.email, current.user.display_name)
        )
        async with self._storage.unit_of_work() as uow:
            fresh = await uow.users.get(current.user.id)
            if fresh is None or fresh.password_hash != current.user.password_hash:
                raise CurrentPasswordIncorrectError("The current password is incorrect.")
            user = fresh.with_password_hash(new_hash)
            await uow.users.save(user)
            await uow.sessions.revoke_all_for_user(user.id, self._clock())
            # The new session keeps this one's level: a password change is not
            # a reason to ask for the second factor again, nor a way to skip it.
            signed_in = new_session(user, user_agent, self._clock(), aal=current.session.aal)
            await uow.sessions.add(signed_in.session)
            await self._audit.record(
                uow, current.actor(request_id), "account.password_changed", "user", str(user.id)
            )
            await uow.commit()
        await self._send_quietly(self._password_changed_message(user))
        return signed_in

    async def request_password_reset(self, email: str, request_id: str | None = None) -> None:
        """Send a reset link if the account exists. Callers answer the same either way."""
        email = normalize_email(email)
        self._enforce(
            await self._limiter.hit(f"reset:email:{email}", _RESETS_PER_EMAIL, _LOGIN_WINDOW)
        )
        now = self._clock()
        token = new_token()
        async with self._storage.unit_of_work() as uow:
            user = await uow.users.get_by_email(email)
            recipient = user if user is not None and not user.is_disabled else None
            if recipient is not None:
                await uow.password_resets.cancel_unused_for_user(recipient.id, now)
                await uow.password_resets.add(
                    PasswordReset(
                        id=uuid.uuid4(),
                        user_id=recipient.id,
                        token_hash=hash_token(token),
                        created_at=now,
                        expires_at=now + PASSWORD_RESET_LIFETIME,
                    )
                )
            await self._audit.record(
                uow,
                Actor(None, request_id),
                "auth.password_reset_requested",
                "account",
                str(user.id) if user is not None else "unknown",
            )
            await uow.commit()
        if recipient is not None:
            await self._send_quietly(
                EmailMessage(
                    to=recipient.email,
                    subject="Reset your Humani-T password",
                    body=(
                        "Someone asked to reset the password for your Humani-T account.\n\n"
                        f"{self._origin}/reset-password#token={token}\n\n"
                        "The link works once and expires in 30 minutes. If it was not you, "
                        "ignore this email; your password has not changed.\n"
                    ),
                )
            )

    async def confirm_password_reset(
        self, *, token: str, new_password: str, request_id: str | None = None
    ) -> None:
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            reset = await uow.password_resets.get_by_token_hash(hash_token(token))
            user = await uow.users.get(reset.user_id) if reset is not None else None
        if reset is None or not reset.is_usable(now) or user is None or user.is_disabled:
            raise InvalidTokenError("This link is invalid or has expired.")

        new_hash = await self._new_hash(new_password, context=(user.email, user.display_name))
        async with self._storage.unit_of_work() as uow:
            # The claim is the race-deciding step: of two simultaneous uses of one
            # link, exactly one gets True.
            if not await uow.password_resets.claim(reset.id, now):
                raise InvalidTokenError("This link is invalid or has expired.")
            fresh = await uow.users.get(user.id)
            if fresh is None or fresh.is_disabled:
                raise InvalidTokenError("This link is invalid or has expired.")
            changed = fresh.with_password_hash(new_hash)
            await uow.users.save(changed)
            await uow.sessions.revoke_all_for_user(changed.id, now)
            await self._audit.record(
                uow,
                Actor(changed.id, request_id),
                "auth.password_reset_completed",
                "user",
                str(changed.id),
            )
            await uow.commit()
        await self._send_quietly(self._password_changed_message(changed))

    async def invite(self, admin: Current, email: str, request_id: str | None) -> Invitation:
        self._require_admin(admin)
        email = normalize_email(email)
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
        async with self._storage.unit_of_work() as uow:
            if await uow.users.get_by_email(email) is not None:
                raise EmailTakenError("An account with this email already exists.")
            await uow.invitations.add(invitation)
            await self._audit.record(
                uow,
                admin.actor(request_id),
                "account.invited",
                "invitation",
                str(invitation.id),
            )
            await uow.commit()
        # Not sent quietly: the admin must learn that the invitation did not go out.
        await self._email.send(
            EmailMessage(
                to=email,
                subject="You have been invited to Humani-T",
                body=(
                    f"{admin.user.display_name} invited you to Humani-T.\n\n"
                    f"{self._origin}/accept-invitation#token={token}\n\n"
                    "The link works once and expires in 7 days.\n"
                ),
            )
        )
        return invitation

    async def register(
        self,
        *,
        token: str,
        password: str,
        display_name: str,
        user_agent: str | None,
        request_id: str | None = None,
    ) -> SignedIn:
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            invitation = await uow.invitations.get_by_token_hash(hash_token(token))
        if invitation is None or not invitation.is_usable(now):
            raise InvalidTokenError("This invitation is invalid or has expired.")

        password_hash = await self._new_hash(password, context=(invitation.email, display_name))
        user = User(
            id=uuid.uuid4(),
            email=invitation.email,
            display_name=display_name.strip(),
            password_hash=password_hash,
            created_at=now,
        )
        async with self._storage.unit_of_work() as uow:
            if not await uow.invitations.claim(invitation.id, now):
                raise InvalidTokenError("This invitation is invalid or has expired.")
            await uow.users.add(user)
            signed_in = new_session(user, user_agent, now, status=self._unenrolled_status())
            await uow.sessions.add(signed_in.session)
            await self._audit.record(
                uow, Actor(user.id, request_id), "account.registered", "user", str(user.id)
            )
            await uow.commit()
        return signed_in

    async def purge_expired(self) -> PurgeReport:
        """Delete ended sessions and spent links, so these tables do not grow forever."""
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            challenges = await uow.webauthn_challenges.delete_spent(now)
            report = PurgeReport(
                sessions=await uow.sessions.delete_ended(now, self.session_policy),
                password_resets=await uow.password_resets.delete_spent(now),
                invitations=await uow.invitations.delete_spent(now),
                webauthn_challenges=challenges,
            )
            await uow.commit()
        return report

    async def _record_failed_login(self, user: User | None, request_id: str | None) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._audit.record(
                uow,
                Actor(None, request_id),
                "auth.login_failed",
                "account",
                str(user.id) if user is not None else "unknown",
            )
            await uow.commit()

    async def _save_if_unchanged(self, uow: UnitOfWork, user: User) -> None:
        # Only for the opportunistic hash upgrade: if the user changed in the
        # meantime, skipping is correct — the upgrade happens at the next login.
        try:
            await uow.users.save(user)
        except ConcurrentUpdateError:
            _log.info("password_rehash_skipped", user_id=str(user.id))

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

    @staticmethod
    def _password_changed_message(user: User) -> EmailMessage:
        return EmailMessage(
            to=user.email,
            subject="Your Humani-T password was changed",
            body=(
                "The password for your Humani-T account was just changed, and every "
                "other signed-in device was signed out.\n\nIf this was not you, reset "
                "your password now and tell your administrator.\n"
            ),
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
