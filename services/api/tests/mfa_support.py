"""Test helpers for the multi-factor specs, built on `tests/support.py`.

`totp_code` is an independent RFC 6238 implementation, so tests compute codes the way an
authenticator app does rather than by calling the code under test. The rest are browser
flows (enrol, sign in with each factor) that the specs repeat.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response

from app.ratelimit.base import RateDecision, RateLimiter
from tests.support import (
    ORIGIN,
    AuthHarness,
    csrf_headers,
    login,
)
from tests.webauthn_authenticator import SoftwareAuthenticator

TOTP_STEP = timedelta(seconds=30)
RP_ID = "testserver"


class SwitchableRateLimiter:
    """A real limiter whose backend can be "unplugged", like Valkey going down.

    While down it raises what every RateLimiter must raise when its backend
    cannot be reached: `RateLimiterUnavailableError`.
    """

    def __init__(self, inner: RateLimiter) -> None:
        self._inner = inner
        self.is_down = False

    def _fail_if_down(self) -> None:
        if self.is_down:
            from app.domain.errors import RateLimiterUnavailableError

            raise RateLimiterUnavailableError("rate limiter backend unreachable")

    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        self._fail_if_down()
        return await self._inner.hit(key, limit, window)

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        self._fail_if_down()
        return await self._inner.check(key, limit, window)

    async def reset(self, key: str) -> None:
        self._fail_if_down()
        await self._inner.reset(key)

    async def close(self) -> None:
        await self._inner.close()


def totp_code(secret_base32: str, at: datetime, *, step_offset: int = 0) -> str:
    """RFC 6238 with the parameters every authenticator app defaults to.

    HMAC-SHA1, 30-second steps from the Unix epoch, six digits, dynamic
    truncation as in RFC 4226 section 5.3.
    """
    key = base64.b32decode(secret_base32.upper() + "=" * (-len(secret_base32) % 8))
    counter = int(at.timestamp()) // 30 + step_offset
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def post(browser: TestClient, path: str, body: Mapping[str, Any] | None = None) -> Response:
    """A state-changing request as the SPA sends it: JSON plus the CSRF header."""
    return browser.post(f"/api/v1{path}", json=body or {}, headers=csrf_headers(browser))


def delete(browser: TestClient, path: str) -> Response:
    return browser.delete(f"/api/v1{path}", headers=csrf_headers(browser))


def reauthenticate(browser: TestClient, password: str) -> Response:
    return post(browser, "/auth/reauthenticate", {"password": password})


def start_totp(browser: TestClient) -> dict[str, Any]:
    response = post(browser, "/account/mfa/totp")
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def confirm_totp(browser: TestClient, code: str) -> Response:
    return post(browser, "/account/mfa/totp/confirm", {"code": code})


def enrol_totp(harness: AuthHarness, browser: TestClient) -> tuple[str, list[str]]:
    """Enrol TOTP on a signed-in browser; return the secret and any recovery codes."""
    secret: str = start_totp(browser)["secret"]
    response = confirm_totp(browser, totp_code(secret, harness.clock()))
    assert response.status_code == 200, response.text
    return secret, response.json()["recovery_codes"] or []


def register_passkey(
    harness: AuthHarness,
    browser: TestClient,
    authenticator: SoftwareAuthenticator,
    name: str = "Laptop",
) -> Response:
    options = post(browser, "/account/mfa/passkeys/options").json()["options"]
    credential = authenticator.create(options, origin=ORIGIN)
    return post(browser, "/account/mfa/passkeys", {"name": name, "credential": credential})


def sign_in_pending(harness: AuthHarness, email: str, password: str) -> TestClient:
    """A browser past the password step, waiting for its second factor."""
    browser = harness.browser()
    response = login(browser, email, password)
    assert response.json().get("status") == "mfa_required", response.text
    return browser


def next_totp(harness: AuthHarness, secret: str) -> str:
    """A code from a step not used before: move the clock on one step first.

    Codes are single use, so a spec that signs in twice must wait for the next
    code exactly as a person would.
    """
    harness.clock.advance(seconds=30)
    return totp_code(secret, harness.clock())


def sign_in_with_totp(
    harness: AuthHarness, email: str, password: str, secret: str
) -> TestClient:
    browser = sign_in_pending(harness, email, password)
    response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})
    assert response.status_code == 200, response.text
    return browser


def user_with_totp(
    harness: AuthHarness, email: str, password: str, *, is_admin: bool = False
) -> tuple[str, list[str]]:
    """Create an account and enrol TOTP on it, as its owner would on first sign-in."""
    harness.create_user(email, password, is_admin=is_admin)
    browser = harness.browser()
    assert login(browser, email, password).json()["status"] == "mfa_enrolment_required"
    return enrol_totp(harness, browser)
