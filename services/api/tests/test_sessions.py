"""Requirements for sessions as domain objects, and for the rate limiter.

Source: NIST SP 800-63B rev 4 (reauthentication at AAL2: 1 hour idle, 24 hours
absolute); OWASP ASVS 5.0 V7 (Session Management) and V6 (throttling).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.sessions import Session, SessionPolicy, hash_token, new_token
from app.ratelimit.memory import MemoryRateLimiter
from tests.support import FakeClock

START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
POLICY = SessionPolicy()


def _session(**overrides: object) -> Session:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "token_hash": hash_token(new_token()),
        "csrf_token_hash": hash_token(new_token()),
        "created_at": START,
        "last_seen_at": START,
        "authenticated_at": START,
    }
    values.update(overrides)
    return Session(**values)  # type: ignore[arg-type]


class TestTokens:
    def test_tokens_carry_at_least_256_bits_and_never_repeat(self):
        tokens = {new_token() for _ in range(1000)}

        assert len(tokens) == 1000
        assert all(len(t) >= 43 for t in tokens)  # 32 bytes, base64url

    def test_only_a_one_way_hash_of_a_token_is_kept(self):
        token = new_token()

        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token).hex()
        assert len(hash_token(token)) == 32


class TestSessionPolicy:
    def test_defaults_follow_nist_aal2(self):
        assert POLICY.idle_timeout == timedelta(hours=1)
        assert POLICY.absolute_timeout == timedelta(hours=24)
        assert POLICY.reauthentication_window == timedelta(minutes=15)


class TestSessionLifetime:
    def test_a_fresh_session_is_active(self):
        assert _session().is_active(START, POLICY)

    def test_an_idle_session_expires_after_one_hour(self):
        session = _session()

        assert session.is_active(START + timedelta(minutes=59), POLICY)
        assert not session.is_active(START + timedelta(minutes=61), POLICY)

    def test_activity_extends_the_idle_window(self):
        session = _session().touched(START + timedelta(minutes=50))

        assert session.is_active(START + timedelta(minutes=100), POLICY)

    def test_no_amount_of_activity_extends_past_24_hours(self):
        session = _session().touched(START + timedelta(hours=23, minutes=59))

        assert not session.is_active(START + timedelta(hours=24, minutes=1), POLICY)

    def test_a_revoked_session_is_never_active(self):
        session = _session().revoked(START)

        assert not session.is_active(START, POLICY)

    def test_sensitive_actions_need_a_sign_in_within_15_minutes(self):
        session = _session()

        assert session.is_recently_authenticated(START + timedelta(minutes=14), POLICY)
        assert not session.is_recently_authenticated(START + timedelta(minutes=16), POLICY)

    def test_timestamps_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _session(created_at=datetime(2026, 1, 1))  # noqa: DTZ001 — the invalid input


class TestMemoryRateLimiter:
    async def test_requests_within_the_limit_are_allowed(self):
        limiter = MemoryRateLimiter(clock=FakeClock())

        for _ in range(5):
            assert (await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))).allowed

    async def test_the_request_over_the_limit_is_refused_with_a_retry_time(self):
        limiter = MemoryRateLimiter(clock=FakeClock())
        for _ in range(5):
            await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))

        decision = await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))

        assert not decision.allowed
        assert timedelta(0) < decision.retry_after <= timedelta(minutes=15)

    async def test_keys_are_counted_separately(self):
        limiter = MemoryRateLimiter(clock=FakeClock())
        for _ in range(5):
            await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))

        assert (await limiter.hit("login:b", limit=5, window=timedelta(minutes=15))).allowed

    async def test_the_window_slides_so_old_requests_stop_counting(self):
        clock = FakeClock()
        limiter = MemoryRateLimiter(clock=clock)
        for _ in range(5):
            await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))

        clock.advance(minutes=16)

        assert (await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))).allowed

    async def test_reset_clears_a_key(self):
        limiter = MemoryRateLimiter(clock=FakeClock())
        for _ in range(5):
            await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))

        await limiter.reset("login:a")

        assert (await limiter.hit("login:a", limit=5, window=timedelta(minutes=15))).allowed
