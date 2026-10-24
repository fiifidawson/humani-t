"""One behaviour contract, run against every StorageProvider implementation.

The Postgres run is skipped unless TEST_DATABASE_URL points at a migrated
database as the runtime role. See CONTRIBUTING for how to start one.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.audit import SYSTEM, AuditLog
from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider
from app.domain.accounts import EmailTakenError, PasswordReset, User
from app.domain.audit import AuditEntry, AuditEvent
from app.domain.encryption import WrappedDataKey
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage

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
    await _truncate(_DATABASE_URL)


async def _truncate(url: str) -> None:
    # audit_events cannot be cleaned up — that is the point of it — so the audit
    # tests below never assume an empty log, only one that grows correctly.
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM data_keys"))
        await connection.execute(text("DELETE FROM password_resets"))
        await connection.execute(text("DELETE FROM sessions"))
        await connection.execute(text("DELETE FROM invitations"))
        await connection.execute(text("DELETE FROM users"))
    await engine.dispose()


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
