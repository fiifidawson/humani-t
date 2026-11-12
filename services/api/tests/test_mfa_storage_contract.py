"""The storage contract for multi-factor credentials, run against every StorageProvider.

Every racing write is conditional and reports whether it won, which is what makes a TOTP
code, a recovery code and a challenge single-use, and stops a replayed passkey assertion
moving its counter. The Postgres run is skipped unless TEST_DATABASE_URL points at a
migrated database as the runtime role.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.mfa import (
    WEBAUTHN_CHALLENGE_LIFETIME,
    ChallengePurpose,
    Passkey,
    PasskeyAlreadyRegisteredError,
    RecoveryCode,
    TotpAlreadyEnrolledError,
    TotpCredential,
    WebAuthnChallenge,
)
from app.domain.sessions import Session, hash_token, new_token
from app.storage.base import StorageProvider, UnitOfWork
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_POSTGRES_ONLY = [
    pytest.mark.postgres,
    pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]


@pytest.fixture(params=["memory", pytest.param("postgres", marks=_POSTGRES_ONLY)])
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[StorageProvider]:
    if request.param == "memory":
        yield MemoryStorage()
        return
    assert _DATABASE_URL is not None
    postgres = PostgresStorage.from_url(_DATABASE_URL)
    yield postgres
    await postgres.close()
    await delete_all_rows(_DATABASE_URL)


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:12]}@example.org",
        display_name="Test",
        password_hash="fake$x",
        created_at=NOW,
    )


def _session(user_id: uuid.UUID, **overrides: object) -> Session:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_hash": hash_token(new_token()),
        "csrf_token_hash": hash_token(new_token()),
        "created_at": NOW,
        "last_seen_at": NOW,
        "authenticated_at": NOW,
    }
    values.update(overrides)
    return Session(**values)  # type: ignore[arg-type]


def _totp(user_id: uuid.UUID) -> TotpCredential:
    return TotpCredential(
        id=uuid.uuid4(), user_id=user_id, secret_sealed=b"sealed-secret", created_at=NOW
    )


def _recovery_codes(user_id: uuid.UUID, count: int = 3) -> list[RecoveryCode]:
    return [
        RecoveryCode(
            id=uuid.uuid4(), user_id=user_id, code_hash=uuid.uuid4().bytes * 2, created_at=NOW
        )
        for _ in range(count)
    ]


def _passkey(user_id: uuid.UUID, *, sign_count: int = 0) -> Passkey:
    return Passkey(
        id=uuid.uuid4(),
        user_id=user_id,
        credential_id=uuid.uuid4().bytes * 2,
        public_key=b"cose-public-key",
        sign_count=sign_count,
        name="Laptop",
        created_at=NOW,
        transports=("internal",),
    )


def _challenge(
    session: Session,
    purpose: ChallengePurpose = ChallengePurpose.AUTHENTICATION,
    *,
    created_at: datetime = NOW,
) -> WebAuthnChallenge:
    return WebAuthnChallenge(
        id=uuid.uuid4(),
        session_id=session.id,
        user_id=session.user_id,
        purpose=purpose,
        challenge=uuid.uuid4().bytes * 2,
        created_at=created_at,
        expires_at=created_at + WEBAUTHN_CHALLENGE_LIFETIME,
    )


async def _with_user(storage: StorageProvider) -> tuple[User, Session]:
    user = _user()
    session = _session(user.id)
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.sessions.add(session)
        await uow.commit()
    return user, session


async def _committed(
    storage: StorageProvider, *, add: Callable[[UnitOfWork], Awaitable[object]]
) -> None:
    async with storage.unit_of_work() as uow:
        await add(uow)
        await uow.commit()


class TestSessionAssuranceIsStored:
    async def test_aal_and_the_pending_flag_survive_a_round_trip(self, storage):
        user = _user()
        pending = _session(user.id, is_mfa_pending=True)
        full = _session(user.id, aal=2)
        async with storage.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.sessions.add(pending)
            await uow.sessions.add(full)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.sessions.get(pending.id) == pending
            assert await uow.sessions.get(full.id) == full


class TestTotpCredentials:
    async def test_a_committed_credential_is_found_by_its_user(self, storage):
        user, _ = await _with_user(storage)
        credential = _totp(user.id)

        await _committed(storage, add=lambda uow: uow.totp_credentials.add(credential))

        async with storage.unit_of_work() as uow:
            assert await uow.totp_credentials.get_for_user(user.id) == credential

    async def test_a_user_without_totp_has_none(self, storage):
        user, _ = await _with_user(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.totp_credentials.get_for_user(user.id) is None

    async def test_a_user_has_at_most_one_totp_credential(self, storage):
        user, _ = await _with_user(storage)
        await _committed(storage, add=lambda uow: uow.totp_credentials.add(_totp(user.id)))

        async with storage.unit_of_work() as uow:
            with pytest.raises(TotpAlreadyEnrolledError):
                await uow.totp_credentials.add(_totp(user.id))

    async def test_confirmation_succeeds_once(self, storage):
        user, _ = await _with_user(storage)
        credential = _totp(user.id)
        await _committed(storage, add=lambda uow: uow.totp_credentials.add(credential))

        async with storage.unit_of_work() as uow:
            first = await uow.totp_credentials.confirm(credential.id, NOW)
            second = await uow.totp_credentials.confirm(credential.id, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.totp_credentials.get_for_user(user.id)

        assert (first, second) == (True, False)
        assert stored is not None
        assert stored.confirmed_at == NOW

    async def test_the_used_step_only_moves_forward(self, storage):
        """The replay guard: a step equal to or older than the last one never wins."""
        user, _ = await _with_user(storage)
        credential = _totp(user.id)
        await _committed(storage, add=lambda uow: uow.totp_credentials.add(credential))

        outcomes = []
        for step in (100, 100, 99, 101):
            async with storage.unit_of_work() as uow:
                outcomes.append(await uow.totp_credentials.advance_step(credential.id, step))
                await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.totp_credentials.get_for_user(user.id)

        assert outcomes == [True, False, False, True]
        assert stored is not None
        assert stored.last_used_step == 101

    async def test_deleting_reports_whether_there_was_anything_to_delete(self, storage):
        user, _ = await _with_user(storage)
        credential = _totp(user.id)
        await _committed(storage, add=lambda uow: uow.totp_credentials.add(credential))

        async with storage.unit_of_work() as uow:
            first = await uow.totp_credentials.delete(credential.id)
            second = await uow.totp_credentials.delete(credential.id)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            assert await uow.totp_credentials.get_for_user(user.id) is None

        assert (first, second) == (True, False)


class TestRecoveryCodes:
    async def test_a_code_can_be_claimed_once(self, storage):
        user, _ = await _with_user(storage)
        codes = _recovery_codes(user.id)
        await _committed(
            storage, add=lambda uow: uow.recovery_codes.replace_for_user(user.id, codes)
        )

        async with storage.unit_of_work() as uow:
            first = await uow.recovery_codes.claim(user.id, codes[0].code_hash, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            second = await uow.recovery_codes.claim(user.id, codes[0].code_hash, NOW)

        assert (first, second) == (True, False)

    async def test_a_code_cannot_be_claimed_for_another_user(self, storage):
        owner, _ = await _with_user(storage)
        other, _ = await _with_user(storage)
        codes = _recovery_codes(owner.id)
        await _committed(
            storage, add=lambda uow: uow.recovery_codes.replace_for_user(owner.id, codes)
        )

        async with storage.unit_of_work() as uow:
            assert not await uow.recovery_codes.claim(other.id, codes[0].code_hash, NOW)

    async def test_the_unused_count_falls_as_codes_are_used(self, storage):
        user, _ = await _with_user(storage)
        codes = _recovery_codes(user.id, count=3)
        await _committed(
            storage, add=lambda uow: uow.recovery_codes.replace_for_user(user.id, codes)
        )

        async with storage.unit_of_work() as uow:
            await uow.recovery_codes.claim(user.id, codes[1].code_hash, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            assert await uow.recovery_codes.count_unused(user.id) == 2

    async def test_replacing_the_set_invalidates_every_old_code(self, storage):
        user, _ = await _with_user(storage)
        old, new = _recovery_codes(user.id), _recovery_codes(user.id)
        await _committed(
            storage, add=lambda uow: uow.recovery_codes.replace_for_user(user.id, old)
        )

        await _committed(
            storage, add=lambda uow: uow.recovery_codes.replace_for_user(user.id, new)
        )

        async with storage.unit_of_work() as uow:
            assert not await uow.recovery_codes.claim(user.id, old[0].code_hash, NOW)
            assert await uow.recovery_codes.claim(user.id, new[0].code_hash, NOW)
            assert await uow.recovery_codes.count_unused(user.id) == len(new) - 1


class TestPasskeys:
    async def test_a_passkey_is_found_by_credential_id_and_listed_for_its_user(self, storage):
        user, _ = await _with_user(storage)
        passkey = _passkey(user.id)

        await _committed(storage, add=lambda uow: uow.passkeys.add(passkey))

        async with storage.unit_of_work() as uow:
            assert await uow.passkeys.get(passkey.id) == passkey
            assert await uow.passkeys.get_by_credential_id(passkey.credential_id) == passkey
            assert await uow.passkeys.list_for_user(user.id) == [passkey]

    async def test_one_credential_id_cannot_be_registered_twice_even_by_another_user(
        self, storage
    ):
        owner, _ = await _with_user(storage)
        other, _ = await _with_user(storage)
        passkey = _passkey(owner.id)
        await _committed(storage, add=lambda uow: uow.passkeys.add(passkey))
        copy = Passkey(
            id=uuid.uuid4(),
            user_id=other.id,
            credential_id=passkey.credential_id,
            public_key=b"another-key",
            sign_count=0,
            name="Copy",
            created_at=NOW,
        )

        async with storage.unit_of_work() as uow:
            with pytest.raises(PasskeyAlreadyRegisteredError):
                await uow.passkeys.add(copy)

    @pytest.mark.parametrize(
        ("stored", "presented", "accepted"),
        [(5, 6, True), (5, 5, False), (5, 4, False), (5, 0, False), (0, 0, True)],
    )
    async def test_a_use_is_recorded_only_when_the_counter_advances(
        self, storage, stored, presented, accepted
    ):
        """Conditional in storage, not only in the domain: two racing assertions with
        the same counter must not both be recorded."""
        user, _ = await _with_user(storage)
        passkey = _passkey(user.id, sign_count=stored)
        await _committed(storage, add=lambda uow: uow.passkeys.add(passkey))

        async with storage.unit_of_work() as uow:
            won = await uow.passkeys.record_use(passkey.id, presented, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            current = await uow.passkeys.get(passkey.id)

        assert won is accepted
        assert current is not None
        assert current.sign_count == (presented if accepted else stored)
        assert current.last_used_at == (NOW if accepted else None)

    async def test_deleting_reports_whether_there_was_anything_to_delete(self, storage):
        user, _ = await _with_user(storage)
        passkey = _passkey(user.id)
        await _committed(storage, add=lambda uow: uow.passkeys.add(passkey))

        async with storage.unit_of_work() as uow:
            first = await uow.passkeys.delete(passkey.id)
            second = await uow.passkeys.delete(passkey.id)
            await uow.commit()

        assert (first, second) == (True, False)


class TestWebAuthnChallenges:
    async def test_a_challenge_is_taken_once(self, storage):
        _, session = await _with_user(storage)
        challenge = _challenge(session)
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(challenge))

        async with storage.unit_of_work() as uow:
            first = await uow.webauthn_challenges.take(session.id, challenge.purpose, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            second = await uow.webauthn_challenges.take(session.id, challenge.purpose, NOW)

        assert first is not None
        assert first.challenge == challenge.challenge
        assert second is None

    async def test_an_expired_challenge_is_not_returned(self, storage):
        _, session = await _with_user(storage)
        challenge = _challenge(session)
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(challenge))

        later = NOW + WEBAUTHN_CHALLENGE_LIFETIME + timedelta(seconds=1)
        async with storage.unit_of_work() as uow:
            assert (
                await uow.webauthn_challenges.take(session.id, challenge.purpose, later) is None
            )

    async def test_a_challenge_belongs_to_one_session_and_one_purpose(self, storage):
        user, session = await _with_user(storage)
        other_session = _session(user.id)
        await _committed(storage, add=lambda uow: uow.sessions.add(other_session))
        challenge = _challenge(session, ChallengePurpose.REGISTRATION)
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(challenge))

        async with storage.unit_of_work() as uow:
            wrong_session = await uow.webauthn_challenges.take(
                other_session.id, ChallengePurpose.REGISTRATION, NOW
            )
            wrong_purpose = await uow.webauthn_challenges.take(
                session.id, ChallengePurpose.AUTHENTICATION, NOW
            )

        assert wrong_session is None
        assert wrong_purpose is None

    async def test_taking_spends_older_challenges_and_returns_only_the_newest(self, storage):
        """Asking for options twice must not leave the first challenge answerable."""
        _, session = await _with_user(storage)
        older = _challenge(session, created_at=NOW)
        newer = _challenge(session, created_at=NOW + timedelta(seconds=10))
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(older))
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(newer))

        async with storage.unit_of_work() as uow:
            taken = await uow.webauthn_challenges.take(
                session.id, older.purpose, NOW + timedelta(seconds=20)
            )
            await uow.commit()
        async with storage.unit_of_work() as uow:
            again = await uow.webauthn_challenges.take(
                session.id, older.purpose, NOW + timedelta(seconds=20)
            )

        assert taken is not None
        assert taken.id == newer.id
        assert again is None

    async def test_spent_and_expired_challenges_are_purged(self, storage):
        _, session = await _with_user(storage)
        spent = _challenge(session, ChallengePurpose.REGISTRATION)
        expired = _challenge(session, created_at=NOW - timedelta(minutes=10))
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(spent))
        await _committed(storage, add=lambda uow: uow.webauthn_challenges.add(expired))
        async with storage.unit_of_work() as uow:
            await uow.webauthn_challenges.take(session.id, spent.purpose, NOW)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            purged = await uow.webauthn_challenges.delete_spent(NOW)
            await uow.commit()

        assert purged == 2


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresSingleUseUnderRaces:
    """Two requests carrying the same secret at the same moment: exactly one wins."""

    async def _race(self, write: Callable[[UnitOfWork], Awaitable[object]]) -> list[bool]:
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)

        async def attempt() -> bool:
            async with postgres.unit_of_work() as uow:
                won = await write(uow)
                await asyncio.sleep(0.05)  # hold the row while the other tries
                await uow.commit()
                return bool(won)

        try:
            return sorted(await asyncio.gather(attempt(), attempt()))
        finally:
            await postgres.close()

    async def _seed(self, add: Callable[[UnitOfWork, User], Awaitable[None]]) -> User:
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        user = _user()
        try:
            async with postgres.unit_of_work() as uow:
                await uow.users.add(user)
                await add(uow, user)
                await uow.commit()
        finally:
            await postgres.close()
        return user

    async def test_one_totp_step_is_accepted_once(self):
        credential_ids: list[uuid.UUID] = []

        async def add(uow: UnitOfWork, user: User) -> None:
            credential = _totp(user.id)
            credential_ids.append(credential.id)
            await uow.totp_credentials.add(credential)

        await self._seed(add)

        outcomes = await self._race(
            lambda uow: uow.totp_credentials.advance_step(credential_ids[0], 1000)
        )

        assert outcomes == [False, True]

    async def test_one_recovery_code_is_accepted_once(self):
        seeded: list[RecoveryCode] = []

        async def add(uow: UnitOfWork, user: User) -> None:
            seeded.extend(_recovery_codes(user.id, count=1))
            await uow.recovery_codes.replace_for_user(user.id, seeded)

        await self._seed(add)
        code = seeded[0]

        outcomes = await self._race(
            lambda uow: uow.recovery_codes.claim(code.user_id, code.code_hash, NOW)
        )

        assert outcomes == [False, True]

    async def test_one_passkey_counter_value_is_accepted_once(self):
        seeded: list[Passkey] = []

        async def add(uow: UnitOfWork, user: User) -> None:
            seeded.append(_passkey(user.id, sign_count=5))
            await uow.passkeys.add(seeded[0])

        await self._seed(add)

        outcomes = await self._race(lambda uow: uow.passkeys.record_use(seeded[0].id, 6, NOW))

        assert outcomes == [False, True]

    async def test_one_challenge_is_taken_once(self):
        seeded: list[WebAuthnChallenge] = []

        async def add(uow: UnitOfWork, user: User) -> None:
            session = _session(user.id)
            await uow.sessions.add(session)
            seeded.append(_challenge(session))
            await uow.webauthn_challenges.add(seeded[0])

        await self._seed(add)
        challenge = seeded[0]

        async def take(uow: UnitOfWork) -> bool:
            taken = await uow.webauthn_challenges.take(
                challenge.session_id, challenge.purpose, NOW
            )
            return taken is not None

        outcomes = await self._race(take)

        assert outcomes == [False, True]
