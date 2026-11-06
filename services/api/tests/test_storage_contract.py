"""One behaviour contract, run against every StorageProvider implementation.

The Postgres run is skipped unless TEST_DATABASE_URL points at a migrated
database as the runtime role. See CONTRIBUTING for how to start one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.accounts import AccountService, Current
from app.application.audit import SYSTEM, AuditLog
from app.application.encryption import ScopeEncryption
from app.auth.fake import FakePasswordHasher
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider
from app.domain.accounts import (
    EmailTakenError,
    ForbiddenError,
    LastAdminError,
    PasswordReset,
    User,
)
from app.domain.audit import AuditEntry, AuditEvent
from app.domain.encryption import WrappedDataKey
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.domain.sessions import Session
from app.email.recording import RecordingEmailProvider
from app.ratelimit.memory import MemoryRateLimiter
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.postgres,
                pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set"),
            ],
        ),
    ]
)
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[StorageProvider]:
    if request.param == "memory":
        yield MemoryStorage()
        return
    assert _DATABASE_URL is not None
    postgres = PostgresStorage.from_url(_DATABASE_URL)
    yield postgres
    await postgres.close()
    await delete_all_rows(_DATABASE_URL)


def _key(scope_id: uuid.UUID | None = None) -> WrappedDataKey:
    return WrappedDataKey(
        scope_id=scope_id or uuid.uuid4(), kek_version=1, wrapped=b"wrapped", created_at=NOW
    )


class TestDataKeys:
    async def test_committed_key_is_visible_to_the_next_unit_of_work(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(key.scope_id) == key

    async def test_uncommitted_work_is_discarded(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)

        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(key.scope_id) is None

    async def test_rollback_discards_changes(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.rollback()
            assert await uow.data_keys.get(key.scope_id) is None

    async def test_missing_scope_is_none(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.data_keys.get(uuid.uuid4()) is None

    async def test_destroying_a_key_is_saved(self, storage):
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.data_keys.save(key.destroyed(NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            stored = await uow.data_keys.get(key.scope_id)
            live = await uow.data_keys.list_live()

        assert stored is not None
        assert stored.is_destroyed
        assert key.scope_id not in {k.scope_id for k in live}

    async def test_saving_an_unknown_key_fails(self, storage):
        async with storage.unit_of_work() as uow:
            with pytest.raises(ValueError, match="does not exist"):
                await uow.data_keys.save(_key())


_AUDIT = AuditLog(ScopeEncryption(LocalKeyProvider({1: bytes(32)}), AesGcmCipher()))


class TestAuditLog:
    async def test_appending_moves_the_head_and_keeps_the_chain_intact(self, storage):
        async with storage.unit_of_work() as uow:
            before = await uow.audit.head()
            event = await _AUDIT.record(uow, SYSTEM, "test.appended", "test", "1")
            await uow.commit()

        async with storage.unit_of_work() as uow:
            head = await uow.audit.head()
            events = await uow.audit.list_events()
            report = await _AUDIT.verify(uow)

        assert head.sequence == before.sequence + 1 == event.sequence
        assert events[-1] == event
        assert report.is_intact

    async def test_an_append_that_does_not_extend_the_head_is_refused(self, storage):
        async with storage.unit_of_work() as uow:
            head = await uow.audit.lock_head()
            entry = AuditEntry(
                id=uuid.uuid4(),
                occurred_at=NOW,
                action="test.forked",
                actor_id=None,
                target_type="test",
                target_id="1",
            )
            stale = AuditEvent.chain(entry, head.sequence + 1, b"" * 32)
            with pytest.raises(ValueError, match="chain head"):
                await uow.audit.append(stale)


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresAuditGuarantees:
    async def test_concurrent_writers_never_fork_the_chain(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)

        async def write(n: int) -> None:
            async with postgres.unit_of_work() as uow:
                await _AUDIT.record(uow, SYSTEM, "test.concurrent", "test", str(n))
                await uow.commit()

        try:
            await asyncio.gather(*(write(n) for n in range(50)))
            async with postgres.unit_of_work() as uow:
                report = await _AUDIT.verify(uow)
        finally:
            await postgres.close()

        assert report.is_intact

    @pytest.mark.parametrize(
        "statement",
        ["UPDATE audit_events SET action = 'x.y'", "DELETE FROM audit_events"],
    )
    async def test_the_app_role_cannot_rewrite_history(self, statement):
        assert _DATABASE_URL is not None
        engine = create_async_engine(_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(text(statement))
        finally:
            await engine.dispose()


class TestRaceSafeWrites:
    """Writes that other requests may race with are conditional, never blind."""

    async def test_a_token_can_be_claimed_only_once(self, storage):
        user = _user()
        reset = _reset(user.id)
        async with storage.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.password_resets.add(reset)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            first = await uow.password_resets.claim(reset.id, NOW)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            second = await uow.password_resets.claim(reset.id, NOW)

        assert (first, second) == (True, False)

    async def test_a_duplicate_email_is_reported_as_taken_not_as_a_crash(self, storage):
        async with storage.unit_of_work() as uow:
            await uow.users.add(_user("dup@example.org"))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(EmailTakenError):
                await uow.users.add(_user("dup@example.org"))


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresConcurrency:
    async def test_concurrent_claims_of_one_token_have_exactly_one_winner(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        user = _user()
        reset = _reset(user.id)
        try:
            async with postgres.unit_of_work() as uow:
                await uow.users.add(user)
                await uow.password_resets.add(reset)
                await uow.commit()

            async def claim() -> bool:
                async with postgres.unit_of_work() as uow:
                    won = await uow.password_resets.claim(reset.id, NOW)
                    await asyncio.sleep(0.05)  # hold the row while the other tries
                    await uow.commit()
                    return won

            outcomes = await asyncio.gather(claim(), claim())
        finally:
            await postgres.close()

        assert sorted(outcomes) == [False, True]

    async def test_stuck_statements_and_idle_transactions_time_out(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        try:
            settings = await postgres.server_settings(
                "statement_timeout", "idle_in_transaction_session_timeout"
            )
        finally:
            await postgres.close()

        assert all(value not in ("0", "") for value in settings.values())

    async def test_the_pool_is_bounded_and_fails_fast(self):
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL, pool_size=2, pool_timeout_seconds=1)
        try:

            async def hold() -> None:
                async with postgres.unit_of_work() as uow:
                    await uow.audit.head()
                    await asyncio.sleep(2)

            holders = [asyncio.create_task(hold()) for _ in range(2 + 0)]
            await asyncio.sleep(0.2)
            with pytest.raises(StorageUnavailableError):
                async with postgres.unit_of_work() as uow:
                    await uow.audit.head()
            await asyncio.gather(*holders)
        finally:
            await postgres.close()


def _user(email: str | None = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:12]}@example.org",
        display_name="Test",
        password_hash="fake$x",
        created_at=NOW,
    )


def _reset(user_id: uuid.UUID) -> PasswordReset:
    return PasswordReset(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=uuid.uuid4().bytes * 2,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


class TestShreddingIsFinal:
    async def test_a_stale_copy_cannot_revive_a_destroyed_key(self, storage):
        """Key rotation reading a key just before a project is shredded must not undo it."""
        key = _key()
        async with storage.unit_of_work() as uow:
            await uow.data_keys.add(key)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stale = await uow.data_keys.get(key.scope_id)
            await uow.data_keys.save(key.destroyed(NOW))
            await uow.commit()
        assert stale is not None

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.data_keys.save(stale.rewrapped(b"rewrapped", 2))

        async with storage.unit_of_work() as uow:
            current = await uow.data_keys.get(key.scope_id)
        assert current is not None
        assert current.is_destroyed


async def _stored_admins(storage: StorageProvider, count: int) -> list[Current]:
    admins = [dataclasses.replace(_user(), is_admin=True) for _ in range(count)]
    async with storage.unit_of_work() as uow:
        for admin in admins:
            await uow.users.add(admin)
        await uow.commit()
    return [_current(admin) for admin in admins]


def _current(user: User) -> Current:
    session = Session(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=uuid.uuid4().bytes * 2,
        csrf_token_hash=uuid.uuid4().bytes * 2,
        created_at=NOW,
        last_seen_at=NOW,
        authenticated_at=NOW,
    )
    return Current(user, session)


def _accounts(storage: StorageProvider) -> AccountService:
    return AccountService(
        storage=storage,
        hasher=FakePasswordHasher(),
        email=RecordingEmailProvider(),
        limiter=MemoryRateLimiter(lambda: NOW),
        audit=_AUDIT,
        public_origin="https://testserver",
        clock=lambda: NOW,
    )


async def _enabled_admin_ids(storage: StorageProvider) -> set[uuid.UUID]:
    async with storage.unit_of_work() as uow:
        return {admin.id for admin in await uow.users.lock_enabled_admins()}


class TestEnabledAdmins:
    async def test_only_enabled_administrators_are_returned(self, storage):
        enabled, disabled = (a.user for a in await _stored_admins(storage, 2))
        user = _user()
        async with storage.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.users.save(disabled.disabled())
            await uow.commit()

        found = await _enabled_admin_ids(storage)

        assert enabled.id in found
        assert disabled.id not in found
        assert user.id not in found


class TestLastAdminUnderConcurrency:
    """Admins disabling one another at the same moment must leave one who can sign in."""

    async def test_two_admins_disabling_each_other_together_leave_one(self, storage):
        accounts = _accounts(storage)
        first, second = await _stored_admins(storage, 2)
        assert await _enabled_admin_ids(storage) == {first.user.id, second.user.id}

        outcomes = await asyncio.gather(
            accounts.disable_user(first, second.user.id),
            accounts.disable_user(second, first.user.id),
            return_exceptions=True,
        )

        assert len(await _enabled_admin_ids(storage)) == 1
        refusals = [o for o in outcomes if isinstance(o, BaseException)]
        assert len(refusals) == 1
        assert isinstance(refusals[0], LastAdminError)

    async def test_a_ring_of_admins_disabling_the_next_always_leaves_one(self, storage):
        accounts = _accounts(storage)
        admins = await _stored_admins(storage, 5)
        targets = admins[1:] + admins[:1]

        outcomes = await asyncio.gather(
            *(
                accounts.disable_user(a, t.user.id)
                for a, t in zip(admins, targets, strict=True)
            ),
            return_exceptions=True,
        )

        left = await _enabled_admin_ids(storage) & {a.user.id for a in admins}
        succeeded = sum(not isinstance(o, BaseException) for o in outcomes)
        assert len(left) >= 1
        assert succeeded == len(admins) - len(left)
        assert all(
            isinstance(o, LastAdminError | ForbiddenError)
            for o in outcomes
            if isinstance(o, BaseException)
        )


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresAdminLock:
    async def test_a_second_locker_waits_until_the_first_commits(self):
        """`lock_enabled_admins` is SELECT … FOR UPDATE: where admin changes serialise."""
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        order: list[str] = []
        first_has_lock = asyncio.Event()
        try:
            await _stored_admins(postgres, 1)

            async def first() -> None:
                async with postgres.unit_of_work() as uow:
                    await uow.users.lock_enabled_admins()
                    order.append("first locked")
                    first_has_lock.set()
                    await asyncio.sleep(0.2)
                    order.append("first committing")
                    await uow.commit()

            async def second() -> None:
                await first_has_lock.wait()
                async with postgres.unit_of_work() as uow:
                    await uow.users.lock_enabled_admins()
                    order.append("second locked")
                    await uow.commit()

            await asyncio.wait_for(asyncio.gather(first(), second()), timeout=5)
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert order == ["first locked", "first committing", "second locked"]
