"""Requirements for cross-site request forgery protection.

Source: OWASP ASVS 5.0 V3 (Web Frontend Security) and the OWASP CSRF Prevention
Cheat Sheet. Three independent layers: the request's origin, a per-session
token, and JSON-only bodies. Any one of them stops a forged request on its own.
"""

from __future__ import annotations

import pytest

from tests.support import (
    ORIGIN,
    USER_EMAIL,
    USER_PASSWORD,
    AuthHarness,
    csrf_headers,
    login,
    make_harness,
)


@pytest.fixture
def harness() -> AuthHarness:
    harness = make_harness()
    harness.create_user(USER_EMAIL, USER_PASSWORD)
    return harness


def _signed_in(harness: AuthHarness):
    browser = harness.browser()
    login(browser, USER_EMAIL, USER_PASSWORD)
    return browser


def _logout(browser, **headers: str):
    return browser.post("/api/v1/auth/logout", headers=headers)


class TestOriginCheck:
    def test_a_request_from_another_site_is_refused_even_at_login(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/login",
            json={"email": USER_EMAIL, "password": USER_PASSWORD},
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403

    def test_a_browser_reporting_a_cross_site_fetch_is_refused(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/login",
            json={"email": USER_EMAIL, "password": USER_PASSWORD},
            headers={"Sec-Fetch-Site": "cross-site"},
        )

        assert response.status_code == 403

    def test_a_same_origin_request_is_allowed(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/login",
            json={"email": USER_EMAIL, "password": USER_PASSWORD},
            headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
        )

        assert response.status_code == 200

    def test_a_non_browser_client_without_origin_headers_can_sign_in(self, harness):
        """curl and Postman send no Origin. Forgery needs a browser, so this is safe;
        the session token check below still applies to everything after sign-in."""
        client = harness.browser()
        client.headers.pop("Origin")

        assert login(client, USER_EMAIL, USER_PASSWORD).status_code == 200

    def test_reading_is_never_blocked(self, harness):
        browser = _signed_in(harness)

        response = browser.get(
            "/api/v1/auth/session", headers={"Origin": "https://evil.example"}
        )

        assert response.status_code == 200


class TestSessionToken:
    def test_a_signed_in_request_without_the_token_is_refused(self, harness):
        browser = _signed_in(harness)

        assert _logout(browser).status_code == 403
        assert browser.get("/api/v1/auth/session").status_code == 200

    def test_a_wrong_token_is_refused(self, harness):
        browser = _signed_in(harness)

        assert _logout(browser, **{"X-CSRF-Token": "guess"}).status_code == 403

    def test_another_sessions_token_is_refused(self, harness):
        mine, theirs = _signed_in(harness), _signed_in(harness)

        assert _logout(mine, **csrf_headers(theirs)).status_code == 403

    def test_the_correct_token_is_accepted(self, harness):
        browser = _signed_in(harness)

        assert _logout(browser, **csrf_headers(browser)).status_code == 204

    def test_the_token_changes_when_the_session_does(self, harness):
        browser = _signed_in(harness)
        first = browser.cookies["__Host-csrf"]
        login(browser, USER_EMAIL, USER_PASSWORD)

        assert browser.cookies["__Host-csrf"] != first


class TestBodyFormat:
    def test_form_posts_are_refused(self, harness):
        """An HTML form can be submitted cross-site without JavaScript; JSON cannot."""
        response = harness.browser().post(
            "/api/v1/auth/login",
            content=f"email={USER_EMAIL}&password={USER_PASSWORD}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 415

    def test_text_plain_is_refused(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/login",
            content='{"email": "a@b.c", "password": "x"}',
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 415
