"""Requirements for behaviour under load: connections, races and memory.

Feature tests send one request at a time; these do not. Each class names the failure it
prevents (OWASP ASVS 5.0 V2, V6 and V13).
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from app.domain.accounts import ThrottledError
from app.domain.mfa import MfaPolicy
from app.storage.base import StorageProvider
from tests.support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    NEW_PASSWORD,
    USER_EMAIL,
    USER_PASSWORD,
    AuthHarness,
    csrf_headers,
    last_link,
    login,
    make_harness,
)


@pytest.fixture
def harness() -> AuthHarness:
    """Load and race specs of the password factor; MFA races are in test_mfa_api.py."""
    harness = make_harness(mfa_policy=MfaPolicy(is_required=False))
    harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
    harness.create_user(USER_EMAIL, USER_PASSWORD)
    return harness


class TestNoConnectionIsHeldDuringSlowWork:
    """A pool of 15 connections must survive a burst of sign-ins.

    Argon2 takes tens of milliseconds and SMTP up to seconds. Holding a pooled
    connection (or the audit chain's lock) across either turns a login burst
    into an outage for every other request.
    """

    def test_password_hashing_never_holds_a_database_connection(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, "wrong-password-entirely")
        login(browser, USER_EMAIL, USER_PASSWORD)
        browser.post(
            "/api/v1/account/password",
            json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
            headers=csrf_headers(browser),
        )
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})
        harness.browser().post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": last_link(harness, "/reset-password"),
                "new_password": "and-yet-another-long-password",
            },
        )

        assert len(harness.hasher.open_units_seen) >= 5
        assert set(harness.hasher.open_units_seen) == {0}

    def test_email_is_sent_only_after_the_transaction_has_ended(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        admin.post(
            "/api/v1/admin/invitations",
            json={"email": "kofi@example.org"},
            headers=csrf_headers(admin),
        )
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})
        user = harness.browser()
        login(user, USER_EMAIL, USER_PASSWORD)
        user.post(
            "/api/v1/account/password",
            json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
            headers=csrf_headers(user),
        )

        assert len(harness.email.open_units_seen) == 3
        assert set(harness.email.open_units_seen) == {0}


class TestThrottleUnderConcurrency:
    async def test_parallel_guesses_cannot_exceed_the_per_account_limit(self, harness):
        """Check-then-count lets 30 simultaneous guesses all pass the check."""
        before = harness.hasher.verify_calls

        outcomes = await asyncio.gather(
            *(
                harness.providers.accounts.login(
                    email=USER_EMAIL,
                    password=f"guess-number-{n:02d}-is-wrong",
                    client_ip=f"198.51.100.{n}",
                    user_agent=None,
                )
                for n in range(30)
            ),
            return_exceptions=True,
        )

        throttled = sum(isinstance(o, ThrottledError) for o in outcomes)
        assert harness.hasher.verify_calls - before <= 10
        assert throttled >= 20


class TestNoLostUpdates:
    async def test_a_late_activity_update_never_revives_a_revoked_session(self, harness):
        """Request A reads the session; the user revokes it; A records activity."""
        signed_in = await harness.providers.accounts.login(
            email=USER_EMAIL, password=USER_PASSWORD, client_ip="203.0.113.1", user_agent=None
        )
        storage = harness.providers.storage

        async with storage.unit_of_work() as uow:
            await uow.sessions.revoke(signed_in.session.id, harness.clock())
            await uow.commit()
        async with storage.unit_of_work() as uow:
            await uow.sessions.touch(signed_in.session.id, harness.clock())
            await uow.commit()

        async with storage.unit_of_work() as uow:
            session = await uow.sessions.get(signed_in.session.id)
        assert session is not None
        assert session.revoked_at is not None

    async def test_a_stale_copy_of_a_user_cannot_overwrite_a_newer_one(self, harness):
        """A login re-hashing a password must not undo an admin's concurrent disable."""
        storage = harness.providers.storage
        async with storage.unit_of_work() as uow:
            stale = await uow.users.get_by_email(USER_EMAIL)
        assert stale is not None

        async with storage.unit_of_work() as uow:
            fresh = await uow.users.get(stale.id)
            assert fresh is not None
            await uow.users.save(fresh.disabled())
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(Exception, match="concurrent"):
                await uow.users.save(stale.with_password_hash("fake$rehashed"))

        async with storage.unit_of_work() as uow:
            current = await uow.users.get(stale.id)
        assert current is not None
        assert current.is_disabled


class TestSingleUseTokens:
    async def test_two_simultaneous_resets_with_one_link_have_one_winner(self, harness):
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})
        token = last_link(harness, "/reset-password")

        outcomes = await asyncio.gather(
            harness.providers.accounts.confirm_password_reset(
                token=token, new_password="first-of-two-racing-passwords"
            ),
            harness.providers.accounts.confirm_password_reset(
                token=token, new_password="second-of-two-racing-passwords"
            ),
            return_exceptions=True,
        )

        assert sum(o is None for o in outcomes) == 1

    async def test_two_simultaneous_registrations_with_one_invitation_have_one_winner(
        self, harness
    ):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        admin.post(
            "/api/v1/admin/invitations",
            json={"email": "kofi@example.org"},
            headers=csrf_headers(admin),
        )
        token = last_link(harness, "/accept-invitation")

        outcomes = await asyncio.gather(
            *(
                harness.providers.accounts.register(
                    token=token,
                    password=NEW_PASSWORD,
                    display_name=f"Kofi {n}",
                    user_agent=None,
                )
                for n in range(2)
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(o, BaseException) for o in outcomes) == 1


class TestBoundedGrowth:
    async def test_expired_sessions_and_spent_tokens_are_purged(self, harness):
        for _ in range(3):
            login(harness.browser(), USER_EMAIL, USER_PASSWORD)
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})

        harness.clock.advance(hours=25)
        report = await harness.providers.accounts.purge_expired()

        assert report.sessions == 3
        assert report.password_resets == 1
        assert harness.storage.state.sessions == {}
        assert harness.storage.state.password_resets == {}

    async def test_purging_keeps_live_sessions(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        await harness.providers.accounts.purge_expired()

        assert browser.get("/api/v1/auth/session").status_code == 200

    async def test_revoking_every_other_session_is_one_bounded_operation(self, harness):
        """Revoking must not walk every session the user ever had."""
        signed_in = await harness.providers.accounts.login(
            email=USER_EMAIL, password=USER_PASSWORD, client_ip="203.0.113.1", user_agent=None
        )
        for _ in range(4):
            await harness.providers.accounts.login(
                email=USER_EMAIL,
                password=USER_PASSWORD,
                client_ip="203.0.113.1",
                user_agent=None,
            )

        async with harness.providers.storage.unit_of_work() as uow:
            revoked = await uow.sessions.revoke_all_for_user(
                signed_in.user.id, harness.clock(), keep=signed_in.session.id
            )
            await uow.commit()

        assert revoked == 4


class TestResourceCleanup:
    async def test_closing_the_providers_closes_every_client_they_opened(self, harness):
        closed: list[str] = []

        class Closing:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                closed.append(self.name)

        providers = dataclasses.replace(
            harness.providers,
            storage=Closing("storage"),  # type: ignore[arg-type]
            rate_limiter=Closing("rate_limiter"),  # type: ignore[arg-type]
        )

        await providers.close()

        assert sorted(closed) == ["rate_limiter", "storage"]


class TestOverloadIsReportedNotCrashed:
    def test_an_oversized_request_body_is_refused_before_it_is_read(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/login",
            content=b'{"email": "' + b"a" * (2 * 1024 * 1024) + b'"}',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_an_unavailable_database_answers_503_with_retry_after(self, harness):
        from app.domain.errors import StorageUnavailableError

        class Exhausted:
            def unit_of_work(self):  # type: ignore[no-untyped-def]
                raise StorageUnavailableError("connection pool exhausted")

            async def is_ready(self) -> bool:
                return False

            async def close(self) -> None:
                return None

        exhausted: StorageProvider = Exhausted()  # type: ignore[assignment]
        harness.app.state.providers = dataclasses.replace(
            harness.providers,
            storage=exhausted,
            accounts=harness.providers.accounts.with_storage(exhausted),
        )

        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.status_code == 503
        assert int(response.headers["retry-after"]) > 0
