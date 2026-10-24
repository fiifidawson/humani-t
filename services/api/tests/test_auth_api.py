"""Requirements for signing in, staying signed in, and getting back in.

Source: WEB_APP_BRIEF rules 6, 9 and 16; NIST SP 800-63B rev 4; OWASP ASVS 5.0
V6 (Authentication), V7 (Session Management) and V16 (Security Logging).

MFA is specified separately (M9). Until then a password sign-in is a full
sign-in.
"""

from __future__ import annotations

import pytest

from tests.support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    NEW_PASSWORD,
    USER_EMAIL,
    USER_PASSWORD,
    AuthHarness,
    cookie_attributes,
    csrf_headers,
    last_link,
    login,
    make_harness,
    plant_session_cookie,
    session_cookie_value,
    set_cookie_headers,
)


@pytest.fixture
def harness() -> AuthHarness:
    harness = make_harness()
    harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
    harness.create_user(USER_EMAIL, USER_PASSWORD)
    return harness


class TestLogin:
    def test_correct_credentials_sign_in_and_return_the_user(self, harness):
        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.status_code == 200
        user = response.json()["user"]
        assert user["email"] == USER_EMAIL
        assert user["is_admin"] is False
        assert "password" not in str(response.json()).lower()

    def test_email_is_matched_case_insensitively(self, harness):
        assert login(harness.browser(), USER_EMAIL.upper(), USER_PASSWORD).status_code == 200

    def test_a_wrong_password_is_refused(self, harness):
        response = login(harness.browser(), USER_EMAIL, "wrong-password-entirely")

        assert response.status_code == 401
        assert "__Host-session" not in set_cookie_headers(response)

    def test_unknown_and_known_accounts_are_indistinguishable(self, harness):
        """Rule: the response must not reveal whether an account exists."""
        known = login(harness.browser(), USER_EMAIL, "wrong-password-entirely")
        unknown = login(harness.browser(), "nobody@example.org", "wrong-password-entirely")

        assert known.status_code == unknown.status_code == 401
        assert known.json() == unknown.json()

    def test_an_unknown_account_still_costs_a_password_verification(self, harness):
        """Rule: equal work on both paths, so response time does not reveal accounts."""
        before = harness.hasher.verify_calls

        login(harness.browser(), "nobody@example.org", "wrong-password-entirely")

        assert harness.hasher.verify_calls == before + 1

    def test_a_disabled_account_cannot_sign_in(self, harness):
        browser = harness.browser()
        login(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
        user_id = _user_id(harness, USER_EMAIL)
        browser.post(f"/api/v1/admin/users/{user_id}/disable", headers=csrf_headers(browser))

        assert login(harness.browser(), USER_EMAIL, USER_PASSWORD).status_code == 401

    def test_malformed_input_is_rejected_before_any_lookup(self, harness):
        browser = harness.browser()

        assert browser.post("/api/v1/auth/login", json={"email": "x"}).status_code == 422
        assert login(browser, "not-an-email", USER_PASSWORD).status_code == 422


class TestSessionCookie:
    def test_the_session_cookie_is_host_bound_secure_and_hidden_from_scripts(self, harness):
        cookies = set_cookie_headers(login(harness.browser(), USER_EMAIL, USER_PASSWORD))
        attributes = cookie_attributes(cookies["__Host-session"])

        assert {"secure", "httponly", "path=/", "samesite=lax"} <= attributes
        assert not any(a.startswith("domain=") for a in attributes)

    def test_the_csrf_cookie_is_readable_by_the_app_but_strictly_same_site(self, harness):
        cookies = set_cookie_headers(login(harness.browser(), USER_EMAIL, USER_PASSWORD))
        attributes = cookie_attributes(cookies["__Host-csrf"])

        assert {"secure", "path=/", "samesite=strict"} <= attributes
        assert "httponly" not in attributes

    def test_a_session_token_is_never_stored_in_the_clear(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        token = browser.cookies["__Host-session"]

        assert token not in repr(harness.providers.storage.state.sessions)  # type: ignore[attr-defined]

    def test_signing_in_always_issues_a_new_token(self, harness):
        """Rule: no session fixation. A token set before sign-in is never adopted."""
        browser = harness.browser()
        plant_session_cookie(browser, "attacker-chosen-value")

        first = session_cookie_value(login(browser, USER_EMAIL, USER_PASSWORD))
        second = session_cookie_value(login(browser, USER_EMAIL, USER_PASSWORD))

        assert first != "attacker-chosen-value"
        assert second != first


class TestCurrentSession:
    def test_a_signed_in_browser_sees_its_user(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        assert browser.get("/api/v1/auth/session").json()["user"]["email"] == USER_EMAIL

    def test_an_anonymous_browser_gets_401(self, harness):
        response = harness.browser().get("/api/v1/auth/session")

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"

    def test_a_forged_token_gets_401(self, harness):
        browser = harness.browser()
        plant_session_cookie(browser, "forged")

        assert browser.get("/api/v1/auth/session").status_code == 401

    def test_a_session_idle_for_over_an_hour_has_ended(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        harness.clock.advance(minutes=61)

        assert browser.get("/api/v1/auth/session").status_code == 401

    def test_activity_keeps_a_session_alive(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        for _ in range(4):
            harness.clock.advance(minutes=50)
            assert browser.get("/api/v1/auth/session").status_code == 200

    def test_a_session_ends_after_24_hours_however_active(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        for _ in range(28):  # 23 h 20 min of steady activity
            harness.clock.advance(minutes=50)
            assert browser.get("/api/v1/auth/session").status_code == 200

        harness.clock.advance(minutes=50)  # 24 h 10 min since sign-in

        assert browser.get("/api/v1/auth/session").status_code == 401


class TestLogout:
    def test_logout_ends_the_session_on_the_server_not_just_in_the_browser(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        replay = harness.browser()
        plant_session_cookie(replay, browser.cookies["__Host-session"])
        assert replay.get("/api/v1/auth/session").status_code == 200  # the copy works...

        response = browser.post("/api/v1/auth/logout", headers=csrf_headers(browser))

        assert response.status_code == 204
        assert replay.get("/api/v1/auth/session").status_code == 401  # ...until logout

    def test_logout_clears_both_cookies(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        cookies = set_cookie_headers(
            browser.post("/api/v1/auth/logout", headers=csrf_headers(browser))
        )

        assert "max-age=0" in cookie_attributes(cookies["__Host-session"])
        assert "max-age=0" in cookie_attributes(cookies["__Host-csrf"])


class TestSessionManagement:
    def test_a_user_can_list_their_sessions_and_see_which_is_this_one(self, harness):
        phone, laptop = harness.browser(), harness.browser()
        login(phone, USER_EMAIL, USER_PASSWORD)
        login(laptop, USER_EMAIL, USER_PASSWORD)

        sessions = laptop.get("/api/v1/account/sessions").json()

        assert len(sessions) == 2
        assert sum(s["current"] for s in sessions) == 1
        assert all("token" not in key for s in sessions for key in s)

    def test_revoking_a_session_signs_that_browser_out(self, harness):
        phone, laptop = harness.browser(), harness.browser()
        login(phone, USER_EMAIL, USER_PASSWORD)
        login(laptop, USER_EMAIL, USER_PASSWORD)
        other = next(
            s for s in laptop.get("/api/v1/account/sessions").json() if not s["current"]
        )

        response = laptop.delete(
            f"/api/v1/account/sessions/{other['id']}", headers=csrf_headers(laptop)
        )

        assert response.status_code == 204
        assert phone.get("/api/v1/auth/session").status_code == 401
        assert laptop.get("/api/v1/auth/session").status_code == 200

    def test_another_users_session_cannot_be_revoked_or_even_seen(self, harness):
        admin, user = harness.browser(), harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        login(user, USER_EMAIL, USER_PASSWORD)
        admins_session = admin.get("/api/v1/account/sessions").json()[0]["id"]

        response = user.delete(
            f"/api/v1/account/sessions/{admins_session}", headers=csrf_headers(user)
        )

        assert response.status_code == 404
        assert admin.get("/api/v1/auth/session").status_code == 200

    def test_sign_out_everywhere_else(self, harness):
        phone, tablet, laptop = harness.browser(), harness.browser(), harness.browser()
        for browser in (phone, tablet, laptop):
            login(browser, USER_EMAIL, USER_PASSWORD)

        laptop.post("/api/v1/account/sessions/revoke-others", headers=csrf_headers(laptop))

        assert phone.get("/api/v1/auth/session").status_code == 401
        assert tablet.get("/api/v1/auth/session").status_code == 401
        assert laptop.get("/api/v1/auth/session").status_code == 200


class TestChangePassword:
    def _change(self, browser, current: str = USER_PASSWORD, new: str = NEW_PASSWORD):
        return browser.post(
            "/api/v1/account/password",
            json={"current_password": current, "new_password": new},
            headers=csrf_headers(browser),
        )

    def test_changing_the_password_signs_out_every_other_session(self, harness):
        phone, laptop = harness.browser(), harness.browser()
        login(phone, USER_EMAIL, USER_PASSWORD)
        login(laptop, USER_EMAIL, USER_PASSWORD)

        assert self._change(laptop).status_code == 204
        assert phone.get("/api/v1/auth/session").status_code == 401
        assert laptop.get("/api/v1/auth/session").status_code == 200
        assert login(harness.browser(), USER_EMAIL, NEW_PASSWORD).status_code == 200
        assert login(harness.browser(), USER_EMAIL, USER_PASSWORD).status_code == 401

    def test_the_current_password_must_be_correct(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        assert self._change(browser, current="not-my-password-at-all").status_code == 403

    def test_the_new_password_must_meet_the_policy(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        response = self._change(browser, new="password")

        assert response.status_code == 422
        assert set(response.json()["violations"]) == {"too_short", "too_common"}

    def test_a_sign_in_older_than_15_minutes_must_be_confirmed_first(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        harness.clock.advance(minutes=16)
        browser.get("/api/v1/auth/session")

        refused = self._change(browser)
        browser.post(
            "/api/v1/auth/reauthenticate",
            json={"password": USER_PASSWORD},
            headers=csrf_headers(browser),
        )

        assert refused.status_code == 401
        assert refused.json()["reauthentication_required"] is True
        assert self._change(browser).status_code == 204

    def test_the_user_is_told_by_email(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)

        self._change(browser)

        assert harness.outbox[-1].to == USER_EMAIL  # type: ignore[attr-defined]
        assert "password was changed" in harness.outbox[-1].subject.lower()  # type: ignore[attr-defined]


class TestInvitations:
    """Registration is invite-only: there is no public sign-up endpoint."""

    def _invite(self, browser, email: str = "kofi@example.org"):
        return browser.post(
            "/api/v1/admin/invitations", json={"email": email}, headers=csrf_headers(browser)
        )

    def test_there_is_no_open_registration(self, harness):
        response = harness.browser().post(
            "/api/v1/auth/register",
            json={"token": "", "password": NEW_PASSWORD, "display_name": "Kofi"},
        )

        assert response.status_code in (400, 422)

    def test_an_admin_invites_by_email_and_the_link_is_not_in_the_response(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)

        response = self._invite(admin)

        assert response.status_code == 201
        token = last_link(harness, "/accept-invitation")
        assert token not in response.text

    def test_only_admins_can_invite(self, harness):
        user = harness.browser()
        login(user, USER_EMAIL, USER_PASSWORD)

        assert self._invite(user).status_code == 403
        assert self._invite(harness.browser()).status_code == 401

    def test_accepting_an_invitation_creates_the_account_and_signs_in(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        self._invite(admin)
        token = last_link(harness, "/accept-invitation")
        newcomer = harness.browser()

        response = newcomer.post(
            "/api/v1/auth/register",
            json={"token": token, "password": NEW_PASSWORD, "display_name": "Kofi"},
        )

        assert response.status_code == 201
        assert response.json()["user"]["email"] == "kofi@example.org"
        assert newcomer.get("/api/v1/auth/session").status_code == 200

    def test_an_invitation_works_once(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        self._invite(admin)
        token = last_link(harness, "/accept-invitation")
        body = {"token": token, "password": NEW_PASSWORD, "display_name": "Kofi"}

        harness.browser().post("/api/v1/auth/register", json=body)

        assert harness.browser().post("/api/v1/auth/register", json=body).status_code == 400

    def test_an_invitation_expires_after_seven_days(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        self._invite(admin)
        token = last_link(harness, "/accept-invitation")

        harness.clock.advance(days=7, minutes=1)
        response = harness.browser().post(
            "/api/v1/auth/register",
            json={"token": token, "password": NEW_PASSWORD, "display_name": "Kofi"},
        )

        assert response.status_code == 400

    def test_inviting_an_existing_account_is_refused(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)

        assert self._invite(admin, USER_EMAIL).status_code == 409

    def test_the_new_password_must_meet_the_policy(self, harness):
        admin = harness.browser()
        login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)
        self._invite(admin)
        token = last_link(harness, "/accept-invitation")

        response = harness.browser().post(
            "/api/v1/auth/register",
            json={"token": token, "password": "short", "display_name": "Kofi"},
        )

        assert response.status_code == 422


class TestPasswordReset:
    def _request(self, email: str, harness: AuthHarness):
        return harness.browser().post("/api/v1/auth/password-reset", json={"email": email})

    def _confirm(self, harness: AuthHarness, token: str, password: str = NEW_PASSWORD):
        return harness.browser().post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": password},
        )

    def test_requesting_a_reset_looks_the_same_for_any_email(self, harness):
        known = self._request(USER_EMAIL, harness)
        unknown = self._request("nobody@example.org", harness)

        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()

    def test_only_a_real_account_is_sent_a_link(self, harness):
        self._request("nobody@example.org", harness)
        assert harness.outbox == []

        self._request(USER_EMAIL, harness)
        assert harness.outbox[-1].to == USER_EMAIL  # type: ignore[attr-defined]

    def test_the_link_carries_the_token_in_the_fragment_so_it_never_reaches_logs(self, harness):
        self._request(USER_EMAIL, harness)

        body: str = harness.outbox[-1].body  # type: ignore[attr-defined]
        assert "https://testserver/reset-password#token=" in body

    def test_a_reset_sets_the_new_password_and_signs_out_everywhere(self, harness):
        phone = harness.browser()
        login(phone, USER_EMAIL, USER_PASSWORD)
        self._request(USER_EMAIL, harness)

        response = self._confirm(harness, last_link(harness, "/reset-password"))

        assert response.status_code == 204
        assert phone.get("/api/v1/auth/session").status_code == 401
        assert login(harness.browser(), USER_EMAIL, NEW_PASSWORD).status_code == 200

    def test_a_reset_link_works_once(self, harness):
        self._request(USER_EMAIL, harness)
        token = last_link(harness, "/reset-password")
        self._confirm(harness, token)

        assert self._confirm(harness, token, "and-yet-another-long-password").status_code == 400

    def test_a_reset_link_expires_after_30_minutes(self, harness):
        self._request(USER_EMAIL, harness)
        token = last_link(harness, "/reset-password")

        harness.clock.advance(minutes=31)

        assert self._confirm(harness, token).status_code == 400

    def test_a_newer_link_replaces_an_older_one(self, harness):
        self._request(USER_EMAIL, harness)
        older = last_link(harness, "/reset-password")
        self._request(USER_EMAIL, harness)

        assert self._confirm(harness, older).status_code == 400

    def test_the_new_password_must_meet_the_policy(self, harness):
        self._request(USER_EMAIL, harness)

        response = self._confirm(harness, last_link(harness, "/reset-password"), "password")

        assert response.status_code == 422

    def test_the_user_is_told_their_password_changed(self, harness):
        self._request(USER_EMAIL, harness)
        self._confirm(harness, last_link(harness, "/reset-password"))

        assert "password was changed" in harness.outbox[-1].subject.lower()  # type: ignore[attr-defined]


class TestThrottling:
    """Throttling, not lockout: an attacker cannot lock a real user out for long."""

    def test_repeated_failures_against_one_account_are_throttled(self, harness):
        for _ in range(10):
            login(harness.browser(), USER_EMAIL, "wrong-password-entirely")

        response = login(harness.browser(), USER_EMAIL, "wrong-password-entirely")

        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0

    def test_while_throttled_even_the_right_password_is_refused(self, harness):
        """Otherwise the throttle itself would confirm a guessed password."""
        for _ in range(11):
            login(harness.browser(), USER_EMAIL, "wrong-password-entirely")

        assert login(harness.browser(), USER_EMAIL, USER_PASSWORD).status_code == 429

    def test_the_throttle_lifts_after_the_window(self, harness):
        for _ in range(11):
            login(harness.browser(), USER_EMAIL, "wrong-password-entirely")

        harness.clock.advance(minutes=16)

        assert login(harness.browser(), USER_EMAIL, USER_PASSWORD).status_code == 200

    def test_unknown_accounts_are_throttled_identically(self, harness):
        for _ in range(10):
            login(harness.browser(), "nobody@example.org", "wrong-password-entirely")

        response = login(harness.browser(), "nobody@example.org", "wrong-password-entirely")

        assert response.status_code == 429

    def test_reset_requests_are_throttled_per_email(self, harness):
        for _ in range(5):
            harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})

        response = harness.browser().post(
            "/api/v1/auth/password-reset", json={"email": USER_EMAIL}
        )

        assert response.status_code == 429
        assert len(harness.outbox) == 5


class TestAuditTrail:
    """Rule 16: who signed in, out, or changed credentials is on record."""

    def _actions(self, harness: AuthHarness) -> list[str]:
        events = harness.providers.storage.state.audit_events  # type: ignore[attr-defined]
        return [e.entry.action for e in events]

    def test_sign_in_sign_out_and_failures_are_recorded(self, harness):
        browser = harness.browser()
        login(browser, USER_EMAIL, "wrong-password-entirely")
        login(browser, USER_EMAIL, USER_PASSWORD)
        browser.post("/api/v1/auth/logout", headers=csrf_headers(browser))

        assert self._actions(harness)[-3:] == ["auth.login_failed", "auth.login", "auth.logout"]

    def test_credential_changes_are_recorded(self, harness):
        self_service = harness.browser()
        login(self_service, USER_EMAIL, USER_PASSWORD)
        self_service.post(
            "/api/v1/account/password",
            json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
            headers=csrf_headers(self_service),
        )
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})

        actions = self._actions(harness)
        assert "account.password_changed" in actions
        assert "auth.password_reset_requested" in actions

    def test_audit_records_never_contain_email_addresses_or_passwords(self, harness):
        browser = harness.browser()
        login(browser, "nobody@example.org", "wrong-password-entirely")
        login(browser, USER_EMAIL, USER_PASSWORD)

        events = repr(harness.providers.storage.state.audit_events)  # type: ignore[attr-defined]
        assert "example.org" not in events
        assert USER_PASSWORD not in events


def _user_id(harness: AuthHarness, email: str) -> str:
    users = harness.providers.storage.state.users  # type: ignore[attr-defined]
    return str(next(u.id for u in users.values() if u.email == email))
