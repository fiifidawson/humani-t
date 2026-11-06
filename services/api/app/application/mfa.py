"""Second factors: enrolling and removing them, and completing a sign-in with one.

Each public method is one use case, shaped like those in `accounts.py`: a
short read, slow or risky work with no transaction open (decrypting,
verifying a WebAuthn signature), a short write whose race-deciding step is a
conditional update, the audit event last, then email after the commit.

Three rules hold throughout:

- **Single use is decided by storage.** A TOTP step, a recovery code and a
  WebAuthn challenge are each spent by one conditional write; of two racing
  requests, exactly one wins.
- **Completing a factor rotates the session.** The pending (or aal 1) session
  is revoked and a new aal 2 session issued, so a token seen before the second
  factor never gains what the second factor grants.
- **Changes to authenticators serialise on the user row.** Each one saves the
  user with its version check first, so two concurrent removals cannot both
  see "two left" and leave none.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID

import structlog

from app.application.accounts import Current, SignedIn, new_session
from app.application.audit import Actor, AuditLog
from app.application.encryption import ScopeEncryption
from app.auth.base import PasskeyVerifier
from app.domain.accounts import NotFoundError, ReauthenticationRequiredError, ThrottledError
from app.domain.encryption import EncryptionContext, KeyDestroyedError, UnknownScopeError
from app.domain.errors import RateLimiterUnavailableError
from app.domain.mfa import (
    WEBAUTHN_CHALLENGE_LIFETIME,
    ChallengePurpose,
    EnrolmentCodeIncorrectError,
    LastAuthenticatorError,
    MfaEnrolment,
    NotEnrolledError,
    Passkey,
    PasskeyRegistrationRejectedError,
    PasskeyVerificationError,
    RecoveryCode,
    SecondFactorRejectedError,
    TotpAlreadyEnrolledError,
    TotpCredential,
    WebAuthnChallenge,
    generate_recovery_codes,
    matching_totp_step,
    new_totp_secret,
    new_webauthn_challenge,
    recovery_code_digest,
    totp_provisioning_uri,
)
from app.domain.notifications import EmailDeliveryError, EmailMessage
from app.domain.sessions import SessionPolicy
from app.email.base import EmailProvider
from app.ratelimit.base import RateLimiter
from app.storage.base import StorageProvider, UnitOfWork

# Like passwords: per account, so a fresh pending session buys no extra guesses.
_SECOND_FACTOR_WINDOW = timedelta(minutes=15)
_SECOND_FACTOR_ATTEMPTS = 10
_TOTP_CIPHERTEXT_PURPOSE = "totp_credentials.secret"
_RECOVERY_KEY_PURPOSE = "recovery-codes/v1"
_REJECTED = "The code or passkey was not accepted."

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MfaStatus:
    """What the account has enrolled. Never a secret, a key or a credential id."""

    totp: TotpCredential | None
    passkeys: tuple[Passkey, ...]
    recovery_codes_remaining: int

    @property
    def is_enrolled(self) -> bool:
        return MfaEnrolment(self.totp is not None, len(self.passkeys)).is_enrolled


@dataclass(frozen=True, slots=True)
class TotpEnrolment:
    """The secret, shown once: as text and as the URI a QR code carries."""

    secret: str
    otpauth_uri: str


@dataclass(frozen=True, slots=True)
class Enrolled:
    """The result of adding an authenticator.

    `recovery_codes` and `signed_in` are set only for the account's first one:
    that is when recovery codes are issued and the session reaches aal 2.
    """

    recovery_codes: tuple[str, ...] | None = None
    signed_in: SignedIn | None = None


@dataclass(frozen=True, slots=True)
class PasskeyAdded:
    passkey: Passkey
    enrolled: Enrolled


class MfaService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        email: EmailProvider,
        limiter: RateLimiter,
        passkeys: PasskeyVerifier,
        session_policy: SessionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._email = email
        self._limiter = limiter
        self._passkeys = passkeys
        self._session_policy = session_policy or SessionPolicy()
        self._clock = clock

    async def status(self, current: Current) -> MfaStatus:
        user_id = current.user.id
        async with self._storage.unit_of_work() as uow:
            totp = await uow.totp_credentials.get_for_user(user_id)
            passkeys = await uow.passkeys.list_for_user(user_id)
            remaining = await uow.recovery_codes.count_unused(user_id)
        confirmed = totp if totp is not None and totp.is_confirmed else None
        return MfaStatus(confirmed, tuple(passkeys), remaining)

    async def start_totp(self, current: Current) -> TotpEnrolment:
        """Create an unconfirmed TOTP credential, replacing any earlier unconfirmed one."""
        self._require_recent_sign_in(current)
        user_id = current.user.id
        secret = new_totp_secret()
        credential_id = uuid.uuid4()
        async with self._storage.unit_of_work() as uow:
            existing = await uow.totp_credentials.get_for_user(user_id)
            if existing is not None and existing.is_confirmed:
                raise TotpAlreadyEnrolledError(
                    "This account already has an authenticator app. Remove it first."
                )
            if existing is not None:
                await uow.totp_credentials.delete(existing.id)
            await self._encryption.ensure_scope(uow, user_id)
            sealed = await self._encryption.encrypt(
                uow, self._totp_context(user_id, credential_id), secret
            )
            await uow.totp_credentials.add(
                TotpCredential(
                    id=credential_id,
                    user_id=user_id,
                    secret_sealed=sealed,
                    created_at=self._clock(),
                )
            )
            await uow.commit()
        return TotpEnrolment(
            secret=base64.b32encode(secret).decode().rstrip("="),
            otpauth_uri=totp_provisioning_uri(secret, account_name=current.user.email),
        )

    async def confirm_totp(
        self, current: Current, code: str, *, user_agent: str | None, request_id: str | None
    ) -> Enrolled:
        user_id = current.user.id
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            credential = await uow.totp_credentials.get_for_user(user_id)
            if credential is None or credential.is_confirmed:
                raise NotFoundError("No authenticator app is waiting to be confirmed.")
            secret = await self._totp_secret(uow, credential)
        step = matching_totp_step(secret, code, now, last_used_step=None)
        if step is None:
            raise EnrolmentCodeIncorrectError(
                "The code is incorrect. Check the time on your phone and try again."
            )

        async with self._storage.unit_of_work() as uow:
            enrolment = await self._lock_enrolment(uow, user_id)
            if not await uow.totp_credentials.confirm(credential.id, now):
                raise EnrolmentCodeIncorrectError(
                    "This authenticator app is already confirmed."
                )
            await uow.totp_credentials.advance_step(credential.id, step)
            enrolled = await self._after_adding(uow, current, enrolment, user_agent)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "account.totp_added",
                "totp_credential",
                str(credential.id),
            )
            await uow.commit()
        await self._send_quietly(_added_message(current.user.email, "An authenticator app"))
        return enrolled

    async def remove_totp(self, current: Current, request_id: str | None) -> None:
        self._require_recent_sign_in(current)
        user_id = current.user.id
        async with self._storage.unit_of_work() as uow:
            enrolment = await self._lock_enrolment(uow, user_id)
            credential = await uow.totp_credentials.get_for_user(user_id)
            if credential is None or not credential.is_confirmed:
                raise NotFoundError("This account has no authenticator app.")
            self._require_another(enrolment)
            await uow.totp_credentials.delete(credential.id)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "account.totp_removed",
                "totp_credential",
                str(credential.id),
            )
            await uow.commit()
        await self._send_quietly(_removed_message(current.user.email, "An authenticator app"))

    async def passkey_registration_options(self, current: Current) -> dict[str, Any]:
        self._require_recent_sign_in(current)
        challenge = await self._issue_challenge(current, ChallengePurpose.REGISTRATION)
        async with self._storage.unit_of_work() as uow:
            existing = await uow.passkeys.list_for_user(current.user.id)
        return self._passkeys.registration_options(
            # The user handle is the account id: stable, and no personal data
            # (WebAuthn §14.6.1), unlike the email address.
            user_handle=current.user.id.bytes,
            user_name=current.user.email,
            display_name=current.user.display_name,
            challenge=challenge,
            exclude=[p.credential_id for p in existing],
        )

    async def register_passkey(
        self,
        current: Current,
        *,
        name: str,
        credential: Mapping[str, Any],
        user_agent: str | None,
        request_id: str | None,
    ) -> PasskeyAdded:
        self._require_recent_sign_in(current)
        now = self._clock()
        challenge = await self._take_challenge(current, ChallengePurpose.REGISTRATION)
        if challenge is None:
            raise PasskeyRegistrationRejectedError("The passkey could not be verified.")
        try:
            verified = self._passkeys.verify_registration(credential, challenge=challenge)
        except PasskeyVerificationError as exc:
            _log.info("passkey_registration_rejected", reason=str(exc))
            raise PasskeyRegistrationRejectedError(
                "The passkey could not be verified."
            ) from exc

        passkey = Passkey(
            id=uuid.uuid4(),
            user_id=current.user.id,
            credential_id=verified.credential_id,
            public_key=verified.public_key,
            sign_count=verified.sign_count,
            name=name.strip(),
            created_at=now,
            transports=verified.transports,
        )
        async with self._storage.unit_of_work() as uow:
            enrolment = await self._lock_enrolment(uow, current.user.id)
            await uow.passkeys.add(passkey)
            enrolled = await self._after_adding(uow, current, enrolment, user_agent)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "account.passkey_added",
                "passkey",
                str(passkey.id),
            )
            await uow.commit()
        await self._send_quietly(_added_message(current.user.email, "A passkey"))
        return PasskeyAdded(passkey, enrolled)

    async def remove_passkey(
        self, current: Current, passkey_id: UUID, request_id: str | None
    ) -> None:
        self._require_recent_sign_in(current)
        async with self._storage.unit_of_work() as uow:
            enrolment = await self._lock_enrolment(uow, current.user.id)
            passkey = await uow.passkeys.get(passkey_id)
            # Another user's passkey is "not found": its existence is not this
            # caller's business.
            if passkey is None or passkey.user_id != current.user.id:
                raise NotFoundError("No such passkey.")
            self._require_another(enrolment)
            await uow.passkeys.delete(passkey_id)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "account.passkey_removed",
                "passkey",
                str(passkey_id),
            )
            await uow.commit()
        await self._send_quietly(_removed_message(current.user.email, "A passkey"))

    async def regenerate_recovery_codes(
        self, current: Current, request_id: str | None
    ) -> tuple[str, ...]:
        self._require_recent_sign_in(current)
        async with self._storage.unit_of_work() as uow:
            enrolment = await self._lock_enrolment(uow, current.user.id)
            if not enrolment.is_enrolled:
                raise NotEnrolledError("Add an authenticator before creating recovery codes.")
            codes = await self._issue_recovery_codes(uow, current.user.id)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "account.recovery_codes_generated",
                "user",
                str(current.user.id),
            )
            await uow.commit()
        await self._send_quietly(
            EmailMessage(
                to=current.user.email,
                subject="New recovery codes for your Humani-T account",
                body=(
                    "New recovery codes were just created for your Humani-T account. "
                    "The old ones no longer work.\n\nIf this was not you, sign in, "
                    "remove any sign-in method you do not recognise, and tell your "
                    "administrator.\n"
                ),
            )
        )
        return codes

    async def verify_totp(
        self, pending: Current, code: str, *, user_agent: str | None, request_id: str | None
    ) -> SignedIn:
        """Complete a pending sign-in with an authenticator-app code."""
        user_id = pending.user.id
        await self._count_attempt(user_id)
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            credential = await uow.totp_credentials.get_for_user(user_id)
            usable = credential if credential is not None and credential.is_confirmed else None
            secret = await self._totp_secret(uow, usable) if usable is not None else None
        step = (
            matching_totp_step(secret, code, now, last_used_step=usable.last_used_step)
            if usable is not None and secret is not None
            else None
        )
        signed_in = None
        if usable is not None and step is not None:
            async with self._storage.unit_of_work() as uow:
                # The replay guard: of two requests carrying this code, one advances.
                if await uow.totp_credentials.advance_step(usable.id, step):
                    signed_in = await self._complete_sign_in(
                        uow, pending, user_agent, request_id, "auth.mfa_totp_succeeded"
                    )
        if signed_in is None:
            await self._reject(pending, request_id)
        await self._reset_attempts(user_id)
        return signed_in

    async def verify_recovery_code(
        self, pending: Current, code: str, *, user_agent: str | None, request_id: str | None
    ) -> SignedIn:
        """Complete a pending sign-in with a recovery code, spending it."""
        user_id = pending.user.id
        await self._count_attempt(user_id)
        now = self._clock()
        signed_in = None
        remaining = 0
        async with self._storage.unit_of_work() as uow:
            key = await self._recovery_key(uow, user_id)
            if key is not None and await uow.recovery_codes.claim(
                user_id, recovery_code_digest(key, code), now
            ):
                remaining = await uow.recovery_codes.count_unused(user_id)
                signed_in = await self._complete_sign_in(
                    uow, pending, user_agent, request_id, "auth.mfa_recovery_code_succeeded"
                )
        if signed_in is None:
            await self._reject(pending, request_id)
        await self._reset_attempts(user_id)
        await self._send_quietly(
            EmailMessage(
                to=pending.user.email,
                subject="A recovery code was used to sign in to your Humani-T account",
                body=(
                    "Someone signed in to your Humani-T account with a recovery code. "
                    f"{remaining} unused code(s) remain.\n\nIf this was not you, reset "
                    "your password, review your sign-in methods, and tell your "
                    "administrator.\n"
                ),
            )
        )
        return signed_in

    async def passkey_sign_in_options(self, pending: Current) -> dict[str, Any]:
        challenge = await self._issue_challenge(pending, ChallengePurpose.AUTHENTICATION)
        async with self._storage.unit_of_work() as uow:
            passkeys = await uow.passkeys.list_for_user(pending.user.id)
        return self._passkeys.authentication_options(
            challenge=challenge, allow=[p.credential_id for p in passkeys]
        )

    async def verify_passkey(
        self,
        pending: Current,
        credential: Mapping[str, Any],
        *,
        user_agent: str | None,
        request_id: str | None,
    ) -> SignedIn:
        """Complete a pending sign-in with a passkey assertion."""
        now = self._clock()
        challenge = await self._take_challenge(pending, ChallengePurpose.AUTHENTICATION)
        passkey = await self._claimed_passkey(pending, credential)
        signed_in = None
        if challenge is not None and passkey is not None:
            try:
                verified = self._passkeys.verify_authentication(
                    credential,
                    challenge=challenge,
                    public_key=passkey.public_key,
                    sign_count=passkey.sign_count,
                )
            except PasskeyVerificationError as exc:
                _log.info("passkey_assertion_rejected", reason=str(exc))
            else:
                async with self._storage.unit_of_work() as uow:
                    # Conditional on the counter still advancing: a cloned key
                    # racing the real one cannot both be recorded.
                    if await uow.passkeys.record_use(passkey.id, verified.sign_count, now):
                        signed_in = await self._complete_sign_in(
                            uow, pending, user_agent, request_id, "auth.mfa_passkey_succeeded"
                        )
        if signed_in is None:
            await self._reject(pending, request_id)
        return signed_in

    async def _complete_sign_in(
        self,
        uow: UnitOfWork,
        pending: Current,
        user_agent: str | None,
        request_id: str | None,
        action: str,
    ) -> SignedIn | None:
        """Swap the pending session for an aal 2 one and commit; `None` if it was used."""
        if not await uow.sessions.revoke(pending.session.id, self._clock()):
            return None
        signed_in = new_session(pending.user, user_agent, self._clock(), aal=2)
        await uow.sessions.add(signed_in.session)
        await self._audit.record(
            uow, pending.actor(request_id), action, "session", str(signed_in.session.id)
        )
        await uow.commit()
        return signed_in

    async def _after_adding(
        self,
        uow: UnitOfWork,
        current: Current,
        before: MfaEnrolment,
        user_agent: str | None,
    ) -> Enrolled:
        """The first authenticator issues recovery codes and lifts the session to aal 2.

        Every other session of the account ends with it: they were opened with
        the password alone, and must not outlive the account gaining a factor.
        """
        if before.is_enrolled:
            return Enrolled()
        codes = await self._issue_recovery_codes(uow, current.user.id)
        await uow.sessions.revoke_all_for_user(current.user.id, self._clock())
        signed_in = new_session(current.user, user_agent, self._clock(), aal=2)
        await uow.sessions.add(signed_in.session)
        return Enrolled(recovery_codes=codes, signed_in=signed_in)

    async def _issue_recovery_codes(self, uow: UnitOfWork, user_id: UUID) -> tuple[str, ...]:
        await self._encryption.ensure_scope(uow, user_id)
        key = await self._encryption.derive_key(uow, user_id, _RECOVERY_KEY_PURPOSE)
        codes = generate_recovery_codes()
        now = self._clock()
        await uow.recovery_codes.replace_for_user(
            user_id,
            [
                RecoveryCode(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    code_hash=recovery_code_digest(key, code),
                    created_at=now,
                )
                for code in codes
            ],
        )
        return codes

    async def _recovery_key(self, uow: UnitOfWork, user_id: UUID) -> bytes | None:
        try:
            return await self._encryption.derive_key(uow, user_id, _RECOVERY_KEY_PURPOSE)
        except (UnknownScopeError, KeyDestroyedError):
            return None

    async def _lock_enrolment(self, uow: UnitOfWork, user_id: UUID) -> MfaEnrolment:
        """Save the user (version-checked) first, then read what is enrolled.

        The save takes the user's row lock and fails with `ConcurrentUpdateError`
        if another authenticator change committed since; the reads after it
        therefore see the latest state.
        """
        user = await uow.users.get(user_id)
        if user is None:
            raise NotFoundError("No such account.")
        await uow.users.save(user)
        totp = await uow.totp_credentials.get_for_user(user_id)
        passkeys = await uow.passkeys.list_for_user(user_id)
        return MfaEnrolment(totp is not None and totp.is_confirmed, len(passkeys))

    @staticmethod
    def _require_another(enrolment: MfaEnrolment) -> None:
        if not enrolment.can_remove_an_authenticator:
            raise LastAuthenticatorError(
                "This is your only second factor. Add another before removing it."
            )

    async def _issue_challenge(self, current: Current, purpose: ChallengePurpose) -> bytes:
        challenge = new_webauthn_challenge()
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            await uow.webauthn_challenges.add(
                WebAuthnChallenge(
                    id=uuid.uuid4(),
                    session_id=current.session.id,
                    user_id=current.user.id,
                    purpose=purpose,
                    challenge=challenge,
                    created_at=now,
                    expires_at=now + WEBAUTHN_CHALLENGE_LIFETIME,
                )
            )
            await uow.commit()
        return challenge

    async def _take_challenge(
        self, current: Current, purpose: ChallengePurpose
    ) -> bytes | None:
        # Committed on its own, before verification: the challenge is spent by
        # this attempt whatever its outcome, so a failed answer cannot be retried.
        async with self._storage.unit_of_work() as uow:
            taken = await uow.webauthn_challenges.take(
                current.session.id, purpose, self._clock()
            )
            await uow.commit()
        return taken.challenge if taken is not None else None

    async def _claimed_passkey(
        self, pending: Current, credential: Mapping[str, Any]
    ) -> Passkey | None:
        try:
            credential_id = self._passkeys.credential_id(credential)
        except PasskeyVerificationError:
            return None
        async with self._storage.unit_of_work() as uow:
            passkey = await uow.passkeys.get_by_credential_id(credential_id)
        return passkey if passkey is not None and passkey.user_id == pending.user.id else None

    async def _totp_secret(self, uow: UnitOfWork, credential: TotpCredential) -> bytes:
        return await self._encryption.decrypt(
            uow, self._totp_context(credential.user_id, credential.id), credential.secret_sealed
        )

    @staticmethod
    def _totp_context(user_id: UUID, credential_id: UUID) -> EncryptionContext:
        return EncryptionContext(user_id, _TOTP_CIPHERTEXT_PURPOSE, str(credential_id))

    async def _reject(self, pending: Current, request_id: str | None) -> NoReturn:
        async with self._storage.unit_of_work() as uow:
            await self._audit.record(
                uow,
                Actor(pending.user.id, request_id),
                "auth.mfa_failed",
                "session",
                str(pending.session.id),
            )
            await uow.commit()
        raise SecondFactorRejectedError(_REJECTED)

    def _require_recent_sign_in(self, current: Current) -> None:
        if not current.session.is_recently_authenticated(self._clock(), self._session_policy):
            raise ReauthenticationRequiredError("Confirm your password to continue.")

    async def _count_attempt(self, user_id: UUID) -> None:
        # Counted before any check, so parallel guesses cannot all slip under it.
        decision = await self._limiter.hit(
            _attempts_key(user_id), _SECOND_FACTOR_ATTEMPTS, _SECOND_FACTOR_WINDOW
        )
        if not decision.allowed:
            raise ThrottledError(decision.retry_after)

    async def _reset_attempts(self, user_id: UUID) -> None:
        # The sign-in has happened; an uncleared counter only expires on its own.
        try:
            await self._limiter.reset(_attempts_key(user_id))
        except RateLimiterUnavailableError:
            _log.warning("rate_limit_reset_failed", exc_info=True)

    async def _send_quietly(self, message: EmailMessage) -> None:
        # The change already happened; a mail outage must not undo or hide it.
        try:
            await self._email.send(message)
        except EmailDeliveryError:
            _log.warning("email_delivery_failed", subject=message.subject, exc_info=True)


def _attempts_key(user_id: UUID) -> str:
    return f"mfa:account:{user_id}"


def _added_message(to: str, what: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="A sign-in method was added to your Humani-T account",
        body=(
            f"{what} was just added as a sign-in method for your Humani-T account.\n\n"
            "If this was not you, sign in, remove it, change your password, and tell "
            "your administrator.\n"
        ),
    )


def _removed_message(to: str, what: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="A sign-in method was removed from your Humani-T account",
        body=(
            f"{what} was just removed from the sign-in methods of your Humani-T "
            "account.\n\nIf this was not you, sign in, review your sign-in methods, "
            "change your password, and tell your administrator.\n"
        ),
    )
