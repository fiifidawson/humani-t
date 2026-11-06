"""Requirements for multi-factor authentication (M9), through the HTTP API.

Source: WEB_APP_BRIEF rule 6; NIST SP 800-63B rev 4 at AAL2 (two factors,
replay resistance, reauthentication, notification of authenticator changes);
RFC 6238 (TOTP); W3C WebAuthn Level 3 (registration and authentication
ceremonies, §7.1 and §7.2); OWASP ASVS 5.0 V6 (Authentication), V7 (Session
Management) and V16 (Security Logging).

The shape of sign-in after M9:

1. `POST /auth/login` checks the password. If the account has an authenticator,
   the answer is `"status": "mfa_required"` with a **pending** session: `aal` 1,
   five minutes long, good only for `/auth/mfa/*` and sign-out.
2. A second factor — a passkey, a TOTP code or a recovery code — completes
   sign-in under a **new** session token at `aal` 2.

MFA is required by default. An account without an authenticator signs in at
`aal` 1 with `"status": "mfa_enrolment_required"` and can reach only enrolment,
sign-out and its own account basics until it enrols one.

Passkey specs use `tests/webauthn_authenticator.py`: real ES256 keys, real
signatures, real CBOR. A server that passes them has verified a ceremony; one
that skips a check fails the forgery that targets it.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from tests.mfa_support import (
    RP_ID,
    SwitchableRateLimiter,
    confirm_totp,
    delete,
    enrol_totp,
    next_totp,
    post,
    reauthenticate,
    register_passkey,
    sign_in_pending,
    sign_in_with_totp,
    start_totp,
    totp_code,
    user_with_totp,
)
from tests.support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    NEW_PASSWORD,
    ORIGIN,
    USER_EMAIL,
    USER_PASSWORD,
    AuthHarness,
    FakeClock,
    csrf_headers,
    last_link,
    login,
    make_harness,
    plant_session_cookie,
    session_cookie_value,
    set_cookie_headers,
)
from tests.webauthn_authenticator import SoftwareAuthenticator, b64url_decode, cbor_encode

_CROCKFORD_CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")


@pytest.fixture
def harness() -> AuthHarness:
    return make_harness()


def _user_id(harness: AuthHarness, email: str) -> Any:
    users = harness.storage.state.users
    return next(u.id for u in users.values() if u.email == email)


def _signed_in_without_mfa(harness: AuthHarness, email: str, password: str) -> TestClient:
    """A fresh account's first sign-in: complete, but at aal 1 and not yet enrolled."""
    browser = harness.browser()
    response = login(browser, email, password)
    assert response.json()["status"] == "mfa_enrolment_required", response.text
    return browser


def _wrong_code(secret: str, harness: AuthHarness) -> str:
    """A six-digit code that is valid at no step the server would accept now."""
    valid = {totp_code(secret, harness.clock(), step_offset=n) for n in (-1, 0, 1)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in valid)


def _mfa_status(browser: TestClient) -> dict[str, Any]:
    response = browser.get("/api/v1/account/mfa")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _session_aal(browser: TestClient) -> int:
    aal: int = browser.get("/api/v1/auth/session").json()["session"]["aal"]
    return aal


def _audit_actions(harness: AuthHarness) -> list[str]:
    return [e.entry.action for e in harness.storage.state.audit_events]


def _cookie_headers(browser: TestClient) -> dict[str, str]:
    """A browser's session, as explicit headers for a concurrent async client."""
    session = browser.cookies.get("__Host-session") or ""
    csrf = browser.cookies.get("__Host-csrf") or ""
    return {
        "Cookie": f"__Host-session={session}; __Host-csrf={csrf}",
        "X-CSRF-Token": csrf,
        "Origin": ORIGIN,
    }


async def _concurrently(
    harness: AuthHarness, requests: list[tuple[str, str, TestClient, dict[str, Any] | None]]
) -> list[int]:
    """Send requests at the same moment, each as its own browser; return the statuses."""
    transport = httpx2.ASGITransport(app=harness.app)
    async with httpx2.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        responses = await asyncio.gather(
            *(
                client.request(
                    method, f"/api/v1{path}", json=body, headers=_cookie_headers(browser)
                )
                for method, path, browser, body in requests
            )
        )
    return [r.status_code for r in responses]


def _user_with_passkey(
    harness: AuthHarness, email: str = USER_EMAIL, password: str = USER_PASSWORD
) -> SoftwareAuthenticator:
    harness.create_user(email, password)
    authenticator = SoftwareAuthenticator()
    response = register_passkey(
        harness, _signed_in_without_mfa(harness, email, password), authenticator
    )
    assert response.status_code == 201, response.text
    return authenticator


def _passkey_sign_in(
    harness: AuthHarness,
    authenticator: SoftwareAuthenticator,
    browser: TestClient,
    *,
    origin: str = ORIGIN,
    **forgery: Any,
):
    options = post(browser, "/auth/mfa/passkey/options").json()["options"]
    assertion = authenticator.get(options, origin=origin, **forgery)
    return post(browser, "/auth/mfa/passkey", {"credential": assertion})


class TestTheTestInstruments:
    """The helpers are checked first, so a red spec below is never a broken instrument."""

    def test_the_reference_totp_matches_rfc_6238(self):
        from datetime import UTC, datetime

        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890", gitleaks:allow

        assert totp_code(secret, datetime.fromtimestamp(59, UTC)) == "287082"
        assert totp_code(secret, datetime.fromtimestamp(1111111109, UTC)) == "081804"

    def test_the_software_authenticator_signs_what_webauthn_says_it_signs(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        authenticator = SoftwareAuthenticator()
        options = {"challenge": "Y2hhbGxlbmdl", "rpId": RP_ID}

        assertion = authenticator.get(options, origin=ORIGIN)["response"]
        signed = (
            b64url_decode(assertion["authenticatorData"])
            + hashlib.sha256(b64url_decode(assertion["clientDataJSON"])).digest()
        )

        authenticator.private_key.public_key().verify(
            b64url_decode(assertion["signature"]), signed, ec.ECDSA(hashes.SHA256())
        )

    def test_the_authenticator_data_carries_rp_hash_flags_and_counter(self):
        authenticator = SoftwareAuthenticator()
        options = {"challenge": "Y2hhbGxlbmdl", "rpId": RP_ID}

        honest = b64url_decode(
            authenticator.get(options, origin=ORIGIN)["response"]["authenticatorData"]
        )
        unverified = b64url_decode(
            authenticator.get(options, origin=ORIGIN, user_verified=False)["response"][
                "authenticatorData"
            ]
        )

        assert honest[:32] == hashlib.sha256(RP_ID.encode()).digest()
        assert honest[32] & 0x05 == 0x05  # user present and verified
        assert unverified[32] & 0x04 == 0  # user verification flag cleared
        assert int.from_bytes(honest[33:37], "big") == 1

    def test_the_cbor_encoder_matches_rfc_8949_examples(self):
        assert cbor_encode(0) == bytes.fromhex("00")
        assert cbor_encode(24) == bytes.fromhex("1818")
        assert cbor_encode(-7) == bytes.fromhex("26")
        assert cbor_encode(1000) == bytes.fromhex("1903e8")
        assert cbor_encode("a") == bytes.fromhex("6161")
        assert cbor_encode({1: 2}) == bytes.fromhex("a10102")


class TestMfaIsRequiredByDefault:
    def test_with_production_defaults_an_unenrolled_account_must_enrol_first(self):
        """`make_harness()` builds providers with no MFA override: production's default."""
        harness = make_harness()
        harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
        browser = harness.browser()

        signed_in = login(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
        invite = browser.post(
            "/api/v1/admin/invitations",
            json={"email": "kofi@example.org"},
            headers=csrf_headers(browser),
        )

        assert signed_in.json()["status"] == "mfa_enrolment_required"
        assert invite.status_code == 403
        assert invite.json()["mfa_enrolment_required"] is True

    def test_an_explicit_policy_can_make_a_password_sign_in_complete(self):
        """For deployments behind an IdP that enforces MFA (M12), and for specs of
        the password factor alone. Never the default."""
        from app.domain.mfa import MfaPolicy

        harness = make_harness(mfa_policy=MfaPolicy(is_required=False))
        harness.create_user(USER_EMAIL, USER_PASSWORD)

        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.json()["status"] == "signed_in"
        assert response.json()["user"]["email"] == USER_EMAIL

    def test_an_enrolled_account_is_asked_for_its_second_factor_even_when_optional(self):
        from app.domain.mfa import MfaPolicy

        harness = make_harness(mfa_policy=MfaPolicy(is_required=False))
        harness.create_user(USER_EMAIL, USER_PASSWORD)
        browser = harness.browser()
        login(browser, USER_EMAIL, USER_PASSWORD)
        enrol_totp(harness, browser)

        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.json()["status"] == "mfa_required"


class TestUntilEnrolled:
    """A complete sign-in without MFA reaches enrolment, sign-out and account basics."""

    @pytest.fixture
    def browser(self, harness: AuthHarness) -> TestClient:
        harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
        return _signed_in_without_mfa(harness, ADMIN_EMAIL, ADMIN_PASSWORD)

    def test_sign_in_says_enrolment_is_required_and_returns_the_user(self, harness):
        harness.create_user(USER_EMAIL, USER_PASSWORD)

        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.status_code == 200
        assert response.json()["status"] == "mfa_enrolment_required"
        assert response.json()["user"]["email"] == USER_EMAIL

    def test_the_session_is_aal1_and_says_enrolment_is_required(self, browser):
        body = browser.get("/api/v1/auth/session").json()

        assert body["session"]["aal"] == 1
        assert body["mfa_enrolment_required"] is True

    @pytest.mark.parametrize(
        ("method", "path", "status"),
        [
            ("GET", "/auth/session", 200),
            ("GET", "/account/sessions", 200),
            ("GET", "/account/mfa", 200),
            ("POST", "/account/mfa/totp", 201),
            ("POST", "/account/mfa/passkeys/options", 200),
        ],
    )
    def test_enrolment_and_account_basics_are_reachable(self, browser, method, path, status):
        response = browser.request(
            method,
            f"/api/v1{path}",
            json={} if method == "POST" else None,
            headers=csrf_headers(browser),
        )

        assert response.status_code == status, response.text

    def test_signing_out_is_reachable(self, browser):
        assert post(browser, "/auth/logout").status_code == 204

    def test_everything_else_is_refused_until_an_authenticator_is_enrolled(self, browser):
        response = post(browser, "/admin/invitations", {"email": "kofi@example.org"})

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["mfa_enrolment_required"] is True

    def test_enrolling_lifts_the_restriction(self, harness, browser):
        enrol_totp(harness, browser)

        response = post(browser, "/admin/invitations", {"email": "kofi@example.org"})

        assert response.status_code == 201

    def test_a_newly_registered_account_starts_unenrolled(self, harness, browser):
        enrol_totp(harness, browser)
        post(browser, "/admin/invitations", {"email": "kofi@example.org"})
        token = last_link(harness, "/accept-invitation")

        response = harness.browser().post(
            "/api/v1/auth/register",
            json={"token": token, "password": NEW_PASSWORD, "display_name": "Kofi"},
        )

        assert response.status_code == 201
        assert response.json()["status"] == "mfa_enrolment_required"


class TestTotpEnrolment:
    @pytest.fixture
    def browser(self, harness: AuthHarness) -> TestClient:
        harness.create_user(USER_EMAIL, USER_PASSWORD)
        return _signed_in_without_mfa(harness, USER_EMAIL, USER_PASSWORD)

    def test_starting_returns_a_160_bit_secret_and_a_scannable_uri(self, browser):
        import base64

        body = start_totp(browser)

        assert len(base64.b32decode(body["secret"] + "=" * (-len(body["secret"]) % 8))) >= 20
        assert body["otpauth_uri"].startswith("otpauth://totp/Humani-T:")
        assert f"secret={body['secret']}" in body["otpauth_uri"]
        assert "issuer=Humani-T" in body["otpauth_uri"]

    def test_the_secret_is_not_active_until_a_code_confirms_it(self, harness, browser):
        start_totp(browser)

        assert _mfa_status(browser)["is_enrolled"] is False
        assert _mfa_status(browser)["totp"] is None
        assert login(harness.browser(), USER_EMAIL, USER_PASSWORD).json()["status"] == (
            "mfa_enrolment_required"
        )

    def test_a_wrong_code_does_not_confirm(self, harness, browser):
        secret = start_totp(browser)["secret"]

        response = confirm_totp(browser, _wrong_code(secret, harness))

        assert response.status_code == 400
        assert _mfa_status(browser)["is_enrolled"] is False

    def test_a_valid_code_activates_totp(self, harness, browser):
        secret = start_totp(browser)["secret"]

        response = confirm_totp(browser, totp_code(secret, harness.clock()))

        assert response.status_code == 200
        status = _mfa_status(browser)
        assert status["is_enrolled"] is True
        assert status["totp"] is not None

    def test_confirming_the_first_authenticator_raises_the_session_to_aal2_under_a_new_token(
        self, harness, browser
    ):
        """Step-up rotates the token, so a token captured at aal 1 never gains aal 2."""
        before = browser.cookies["__Host-session"]
        secret = start_totp(browser)["secret"]

        response = confirm_totp(browser, totp_code(secret, harness.clock()))

        assert session_cookie_value(response) != before
        assert _session_aal(browser) == 2
        replay = harness.browser()
        plant_session_cookie(replay, before)
        assert replay.get("/api/v1/auth/session").status_code == 401

    def test_the_secret_is_shown_once_and_never_again(self, harness, browser):
        secret, _ = enrol_totp(harness, browser)

        assert secret not in browser.get("/api/v1/account/mfa").text
        assert post(browser, "/account/mfa/totp").status_code == 409

    def test_restarting_an_unconfirmed_enrolment_replaces_the_secret(self, harness, browser):
        abandoned = start_totp(browser)["secret"]
        current = start_totp(browser)["secret"]

        stale = confirm_totp(browser, totp_code(abandoned, harness.clock()))
        fresh = confirm_totp(browser, totp_code(current, harness.clock()))

        assert abandoned != current
        assert stale.status_code == 400
        assert fresh.status_code == 200

    def test_the_secret_is_stored_only_encrypted_under_the_users_own_key(
        self, harness, browser
    ):
        """Envelope encryption, scope = the user: readable with the KEK, not without."""
        import base64

        from app.domain.encryption import EncryptionContext

        secret, _ = enrol_totp(harness, browser)
        raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
        credentials = harness.storage.state.totp_credentials
        stored = next(iter(credentials.values()))

        assert secret not in repr(credentials)
        assert raw not in stored.secret_sealed

        async def decrypt() -> bytes:
            async with harness.storage.unit_of_work() as uow:
                return await harness.providers.encryption.decrypt(
                    uow,
                    EncryptionContext(
                        stored.user_id, "totp_credentials.secret", str(stored.id)
                    ),
                    stored.secret_sealed,
                )

        assert asyncio.run(decrypt()) == raw

    def test_adding_totp_needs_a_recent_sign_in(self, harness, browser):
        harness.clock.advance(minutes=16)
        browser.get("/api/v1/auth/session")

        refused = post(browser, "/account/mfa/totp")
        reauthenticate(browser, USER_PASSWORD)

        assert refused.status_code == 401
        assert refused.json()["reauthentication_required"] is True
        assert post(browser, "/account/mfa/totp").status_code == 201

    def test_the_rotated_session_keeps_its_second_factor_level(self, harness, browser):
        """Step-up issues a new token, but a password alone never changes the level."""
        enrol_totp(harness, browser)
        assert _session_aal(browser) == 2

        assert reauthenticate(browser, USER_PASSWORD).status_code == 204

        assert _session_aal(browser) == 2
        assert browser.get("/api/v1/projects").status_code == 200

    def test_the_user_is_emailed_once_totp_is_added_and_not_before(self, harness, browser):
        secret = start_totp(browser)["secret"]
        assert harness.outbox == []

        confirm_totp(browser, totp_code(secret, harness.clock()))

        message = harness.outbox[-1]
        assert message.to == USER_EMAIL  # type: ignore[attr-defined]
        assert "added" in message.subject.lower()  # type: ignore[attr-defined]
        assert secret not in message.body  # type: ignore[attr-defined]
        assert set(harness.email.open_units_seen) == {0}


class TestTwoStepSignIn:
    @pytest.fixture
    def secret(self, harness: AuthHarness) -> str:
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        return secret

    def test_a_correct_password_yields_a_pending_session_and_the_methods(self, harness, secret):
        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "mfa_required"
        assert set(body["methods"]) == {"totp", "recovery_code"}
        assert "user" not in body
        assert "__Host-session" in set_cookie_headers(response)

    def test_a_wrong_password_looks_the_same_with_or_without_mfa(self, harness, secret):
        harness.create_user("kofi@example.org", NEW_PASSWORD)

        enrolled = login(harness.browser(), USER_EMAIL, "wrong-password-entirely")
        unenrolled = login(harness.browser(), "kofi@example.org", "wrong-password-entirely")

        assert enrolled.status_code == unenrolled.status_code == 401
        assert enrolled.json() == unenrolled.json()

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/auth/session", None),
            ("GET", "/account/sessions", None),
            ("GET", "/account/mfa", None),
            ("POST", "/account/mfa/totp", {}),
            ("POST", "/account/mfa/passkeys/options", {}),
            ("POST", "/account/mfa/recovery-codes", {}),
            ("POST", "/auth/reauthenticate", {"password": USER_PASSWORD}),
            (
                "POST",
                "/account/password",
                {"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
            ),
            ("POST", "/admin/invitations", {"email": "kofi@example.org"}),
        ],
    )
    def test_a_pending_session_reaches_nothing_but_the_mfa_endpoints(
        self, harness, secret, method, path, body
    ):
        """Above all, it cannot enrol a new authenticator: that would bypass the factor."""
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = browser.request(
            method, f"/api/v1{path}", json=body, headers=csrf_headers(browser)
        )

        assert response.status_code == 401
        assert response.json()["mfa_required"] is True

    def test_a_pending_session_can_be_abandoned(self, harness, secret):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        assert post(browser, "/auth/logout").status_code == 204

    def test_a_valid_code_completes_sign_in_at_aal2(self, harness, secret):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 200
        assert response.json()["status"] == "signed_in"
        assert response.json()["user"]["email"] == USER_EMAIL
        assert _session_aal(browser) == 2

    def test_completing_mfa_issues_a_new_token_and_ends_the_pending_one(self, harness, secret):
        """No fixation: whoever holds the pending token gains nothing from the second step."""
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        pending_token = browser.cookies["__Host-session"]
        pending_csrf = browser.cookies["__Host-csrf"]

        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert session_cookie_value(response) != pending_token
        replay = harness.browser()
        plant_session_cookie(replay, pending_token)
        replay.cookies.set("__Host-csrf", pending_csrf, domain="testserver.local")
        retry = post(replay, "/auth/mfa/totp", {"code": next_totp(harness, secret)})
        assert retry.status_code == 401
        assert "mfa_required" not in retry.json()

    def test_a_wrong_code_is_refused_and_the_pending_session_may_try_again(
        self, harness, secret
    ):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        wrong = post(browser, "/auth/mfa/totp", {"code": _wrong_code(secret, harness)})
        right = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert wrong.status_code == 401
        assert right.status_code == 200

    def test_a_pending_session_expires_after_five_minutes(self, harness, secret):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        harness.clock.advance(minutes=5, seconds=1)
        response = post(browser, "/auth/mfa/totp", {"code": totp_code(secret, harness.clock())})

        assert response.status_code == 401

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef"])
    def test_a_malformed_code_is_rejected_at_the_boundary(self, harness, secret, code):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        assert post(browser, "/auth/mfa/totp", {"code": code}).status_code == 422

    def test_changing_the_password_keeps_the_session_at_aal2(self, harness, secret):
        """The password change rotates the session; rotation must not drop the level."""
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)

        post(
            browser,
            "/account/password",
            {"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
        )

        assert _session_aal(browser) == 2


class TestTotpIsSingleUse:
    @pytest.fixture
    def secret(self, harness: AuthHarness) -> str:
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        return secret

    def test_a_code_cannot_be_used_twice(self, harness, secret):
        code = next_totp(harness, secret)
        first = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/totp",
            {"code": code},
        )

        second = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/totp",
            {"code": code},
        )

        assert first.status_code == 200
        assert second.status_code == 401

    def test_an_older_code_is_refused_once_a_newer_one_was_used(self, harness, secret):
        harness.clock.advance(seconds=60)
        newer = totp_code(secret, harness.clock(), step_offset=1)
        older = totp_code(secret, harness.clock())
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/totp",
            {"code": newer},
        )

        response = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/totp",
            {"code": older},
        )

        assert response.status_code == 401

    @pytest.mark.parametrize(("offset", "status"), [(-1, 200), (1, 200), (-2, 401), (2, 401)])
    def test_one_step_of_clock_drift_is_tolerated_and_no_more(
        self, harness, secret, offset, status
    ):
        harness.clock.advance(minutes=2)
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = post(
            browser,
            "/auth/mfa/totp",
            {"code": totp_code(secret, harness.clock(), step_offset=offset)},
        )

        assert response.status_code == status

    async def test_two_simultaneous_sign_ins_with_one_code_have_one_winner(
        self, harness, secret
    ):
        code = next_totp(harness, secret)
        first = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        second = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        statuses = await _concurrently(
            harness,
            [
                ("POST", "/auth/mfa/totp", first, {"code": code}),
                ("POST", "/auth/mfa/totp", second, {"code": code}),
            ],
        )

        assert sorted(statuses) == [200, 401]


class TestSecondFactorThrottling:
    """Throttled like passwords: per account, so fresh pending sessions do not help."""

    @pytest.fixture
    def secret(self, harness: AuthHarness) -> str:
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        return secret

    def test_repeated_wrong_codes_are_throttled_and_then_even_a_right_one_is_refused(
        self, harness, secret
    ):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        for _ in range(10):
            post(browser, "/auth/mfa/totp", {"code": _wrong_code(secret, harness)})

        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0

    def test_failures_from_different_pending_sessions_count_together(self, harness, secret):
        for _ in range(2):
            browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
            for _ in range(5):
                post(browser, "/auth/mfa/totp", {"code": _wrong_code(secret, harness)})

        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 429

    def test_wrong_recovery_codes_count_against_the_same_limit(self, harness, secret):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        for _ in range(10):
            post(browser, "/auth/mfa/recovery-code", {"code": "0000-0000-0000-0000"})

        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 429

    def test_the_throttle_lifts_after_the_window(self, harness, secret):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        for _ in range(11):
            post(browser, "/auth/mfa/totp", {"code": _wrong_code(secret, harness)})

        harness.clock.advance(minutes=16)
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        response = post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 200


class TestRecoveryCodes:
    @pytest.fixture
    def enrolled(self, harness: AuthHarness) -> tuple[str, list[str]]:
        return user_with_totp(harness, USER_EMAIL, USER_PASSWORD)

    def test_the_first_enrolment_issues_ten_codes_of_at_least_64_bits(self, enrolled):
        _, codes = enrolled

        assert len(codes) == 10
        assert len(set(codes)) == 10
        assert all(_CROCKFORD_CODE.match(code) for code in codes)  # 16 x 5 = 80 bits

    def test_codes_are_stored_only_as_keyed_hashes(self, harness, enrolled):
        _, codes = enrolled
        stored = harness.storage.state.recovery_codes
        normalised = [code.replace("-", "") for code in codes]
        unkeyed = {hashlib.sha256(code.encode()).digest() for code in normalised}

        assert len(stored) == 10
        assert not any(code in repr(stored) for code in codes + normalised)
        assert not unkeyed & {row.code_hash for row in stored.values()}

    def test_a_recovery_code_completes_sign_in_at_aal2(self, harness, enrolled):
        _, codes = enrolled
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = post(browser, "/auth/mfa/recovery-code", {"code": codes[0]})

        assert response.status_code == 200
        assert response.json()["status"] == "signed_in"
        assert _session_aal(browser) == 2

    def test_a_code_works_once(self, harness, enrolled):
        _, codes = enrolled
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        response = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        assert response.status_code == 401

    def test_a_code_may_be_typed_in_lower_case_without_dashes(self, harness, enrolled):
        _, codes = enrolled
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = post(
            browser, "/auth/mfa/recovery-code", {"code": codes[1].replace("-", "").lower()}
        )

        assert response.status_code == 200

    def test_using_a_code_tells_the_user_by_email_how_many_remain(self, harness, enrolled):
        _, codes = enrolled
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        message = harness.outbox[-1]
        assert message.to == USER_EMAIL  # type: ignore[attr-defined]
        assert "recovery code was used" in message.subject.lower()  # type: ignore[attr-defined]
        assert "9 unused" in message.body  # type: ignore[attr-defined]
        assert codes[0] not in message.body  # type: ignore[attr-defined]
        assert set(harness.email.open_units_seen) == {0}

    def test_codes_die_with_the_users_data_key(self, harness, enrolled):
        """The HMAC key is derived from the user's data key: shredding it ends the codes."""
        _, codes = enrolled
        user_id = _user_id(harness, USER_EMAIL)

        async def shred() -> None:
            async with harness.storage.unit_of_work() as uow:
                await harness.providers.encryption.destroy_scope(uow, user_id)
                await uow.commit()

        asyncio.run(shred())
        response = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        assert response.status_code == 401

    def test_the_number_of_unused_codes_is_visible(self, harness, enrolled):
        secret, codes = enrolled
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)

        assert _mfa_status(browser)["recovery_codes_remaining"] == 9

    def test_regenerating_replaces_every_old_code(self, harness, enrolled):
        secret, old = enrolled
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)

        response = post(browser, "/account/mfa/recovery-codes")

        assert response.status_code == 201
        new = response.json()["recovery_codes"]
        assert len(new) == 10
        assert not set(new) & set(old)
        assert _mfa_status(browser)["recovery_codes_remaining"] == 10
        stale = post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": old[0]},
        )
        assert stale.status_code == 401

    def test_regenerating_needs_a_recent_sign_in(self, harness, enrolled):
        secret, _ = enrolled
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)
        harness.clock.advance(minutes=16)
        browser.get("/api/v1/auth/session")

        response = post(browser, "/account/mfa/recovery-codes")

        assert response.status_code == 401
        assert response.json()["reauthentication_required"] is True

    def test_regenerating_tells_the_user_by_email(self, harness, enrolled):
        secret, _ = enrolled
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)

        new = post(browser, "/account/mfa/recovery-codes").json()["recovery_codes"]

        message = harness.outbox[-1]
        assert message.to == USER_EMAIL  # type: ignore[attr-defined]
        assert "recovery codes" in message.subject.lower()  # type: ignore[attr-defined]
        assert not any(code in message.body for code in new)  # type: ignore[attr-defined]

    async def test_two_simultaneous_uses_of_one_code_have_one_winner(self, harness, enrolled):
        _, codes = enrolled
        first = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        second = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        statuses = await _concurrently(
            harness,
            [
                ("POST", "/auth/mfa/recovery-code", first, {"code": codes[0]}),
                ("POST", "/auth/mfa/recovery-code", second, {"code": codes[0]}),
            ],
        )

        assert sorted(statuses) == [200, 401]


class TestPasskeyRegistration:
    @pytest.fixture
    def browser(self, harness: AuthHarness) -> TestClient:
        harness.create_user(USER_EMAIL, USER_PASSWORD)
        return _signed_in_without_mfa(harness, USER_EMAIL, USER_PASSWORD)

    def _options(self, browser: TestClient) -> dict[str, Any]:
        response = post(browser, "/account/mfa/passkeys/options")
        assert response.status_code == 200, response.text
        options: dict[str, Any] = response.json()["options"]
        return options

    def _register(self, browser: TestClient, credential: dict[str, Any], name: str = "Laptop"):
        return post(browser, "/account/mfa/passkeys", {"name": name, "credential": credential})

    def test_options_name_this_deployment_as_the_relying_party(self, browser):
        options = self._options(browser)

        assert options["rp"]["id"] == RP_ID
        assert options["rp"]["name"] == "Humani-T"

    def test_options_require_user_verification_and_offer_es256(self, browser):
        options = self._options(browser)

        assert options["authenticatorSelection"]["userVerification"] == "required"
        assert -7 in {p["alg"] for p in options["pubKeyCredParams"]}
        assert options["attestation"] == "none"

    def test_options_carry_a_fresh_challenge_of_at_least_16_bytes(self, browser):
        first, second = self._options(browser), self._options(browser)

        assert len(b64url_decode(first["challenge"])) >= 16
        assert first["challenge"] != second["challenge"]

    def test_the_user_handle_is_opaque(self, browser):
        """WebAuthn §14.6.1: the handle must not contain personal data such as the email."""
        handle = b64url_decode(self._options(browser)["user"]["id"])

        assert USER_EMAIL.encode() not in handle
        assert 16 <= len(handle) <= 64

    def test_the_relying_party_id_follows_the_configured_public_origin(self):
        origin = "https://humani-t.example.org"
        harness = make_harness(public_origin=origin)
        harness.create_user(USER_EMAIL, USER_PASSWORD)
        browser = TestClient(harness.app, base_url=origin, headers={"Origin": origin})
        login(browser, USER_EMAIL, USER_PASSWORD)

        options = post(browser, "/account/mfa/passkeys/options").json()["options"]

        assert options["rp"]["id"] == "humani-t.example.org"

    def test_a_genuine_registration_is_stored_and_listed(self, browser):
        authenticator = SoftwareAuthenticator()
        credential = authenticator.create(self._options(browser), origin=ORIGIN)

        response = self._register(browser, credential, name="Work laptop")

        assert response.status_code == 201
        assert response.json()["passkey"]["name"] == "Work laptop"
        passkeys = _mfa_status(browser)["passkeys"]
        assert [p["name"] for p in passkeys] == ["Work laptop"]
        assert all("public_key" not in p and "credential_id" not in p for p in passkeys)

    def test_a_first_passkey_issues_recovery_codes_and_raises_the_session_to_aal2(
        self, browser
    ):
        before = browser.cookies["__Host-session"]
        credential = SoftwareAuthenticator().create(self._options(browser), origin=ORIGIN)

        response = self._register(browser, credential)

        assert len(response.json()["recovery_codes"]) == 10
        assert session_cookie_value(response) != before
        assert _session_aal(browser) == 2

    def test_a_second_authenticator_does_not_issue_new_recovery_codes(self, harness, browser):
        enrol_totp(harness, browser)
        credential = SoftwareAuthenticator().create(self._options(browser), origin=ORIGIN)

        response = self._register(browser, credential)

        assert response.status_code == 201
        assert response.json()["recovery_codes"] is None
        assert _mfa_status(browser)["recovery_codes_remaining"] == 10

    @pytest.mark.parametrize(
        "forgery",
        [
            pytest.param({"origin": "https://evil.example"}, id="another-origin"),
            pytest.param({"rp_id": "evil.example"}, id="another-relying-party"),
            pytest.param({"user_verified": False}, id="no-user-verification"),
            pytest.param({"ceremony_type": "webauthn.get"}, id="an-assertion-not-a-creation"),
            pytest.param(
                {"challenge": "bm90LWlzc3VlZC1ieS10aGUtc2VydmVy"}, id="unissued-challenge"
            ),
        ],
    )
    def test_a_forged_registration_is_refused(self, browser, forgery):
        options = self._options(browser)
        forgery = dict(forgery)
        origin = forgery.pop("origin", ORIGIN)
        credential = SoftwareAuthenticator().create(options, origin=origin, **forgery)

        response = self._register(browser, credential)

        assert response.status_code == 400
        assert _mfa_status(browser)["passkeys"] == []

    def test_a_challenge_answers_one_registration_only(self, browser):
        options = self._options(browser)
        self._register(browser, SoftwareAuthenticator().create(options, origin=ORIGIN))

        replay = self._register(
            browser, SoftwareAuthenticator().create(options, origin=ORIGIN), name="Second"
        )

        assert replay.status_code == 400

    def test_a_challenge_expires_after_five_minutes(self, harness, browser):
        options = self._options(browser)
        harness.clock.advance(minutes=5, seconds=1)

        response = self._register(
            browser, SoftwareAuthenticator().create(options, origin=ORIGIN)
        )

        assert response.status_code == 400

    def test_a_challenge_issued_to_one_session_is_refused_in_another(self, harness, browser):
        options = self._options(browser)
        other = _signed_in_without_mfa(harness, USER_EMAIL, USER_PASSWORD)

        response = self._register(other, SoftwareAuthenticator().create(options, origin=ORIGIN))

        assert response.status_code == 400

    def test_one_authenticator_cannot_be_registered_twice(self, browser):
        authenticator = SoftwareAuthenticator()
        self._register(browser, authenticator.create(self._options(browser), origin=ORIGIN))

        again = self._register(
            browser, authenticator.create(self._options(browser), origin=ORIGIN), name="Again"
        )

        assert again.status_code == 409

    def test_registered_passkeys_are_excluded_from_new_options(self, browser):
        authenticator = SoftwareAuthenticator()
        self._register(browser, authenticator.create(self._options(browser), origin=ORIGIN))

        excluded = {c["id"] for c in self._options(browser)["excludeCredentials"]}

        assert excluded == {authenticator.credential_id_b64}

    def test_adding_a_passkey_needs_a_recent_sign_in(self, harness, browser):
        harness.clock.advance(minutes=16)
        browser.get("/api/v1/auth/session")

        response = post(browser, "/account/mfa/passkeys/options")

        assert response.status_code == 401
        assert response.json()["reauthentication_required"] is True

    def test_the_user_is_emailed_when_a_passkey_is_added(self, harness, browser):
        self._register(
            browser, SoftwareAuthenticator().create(self._options(browser), origin=ORIGIN)
        )

        message = harness.outbox[-1]
        assert message.to == USER_EMAIL  # type: ignore[attr-defined]
        assert "added" in message.subject.lower()  # type: ignore[attr-defined]


class TestPasskeySignIn:
    @pytest.fixture
    def authenticator(self, harness: AuthHarness) -> SoftwareAuthenticator:
        return _user_with_passkey(harness)

    def test_sign_in_offers_the_passkey(self, harness, authenticator):
        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert "passkey" in response.json()["methods"]

    def test_options_allow_only_this_users_credentials_and_require_verification(
        self, harness, authenticator
    ):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        options = post(browser, "/auth/mfa/passkey/options").json()["options"]

        assert options["rpId"] == RP_ID
        assert options["userVerification"] == "required"
        assert [c["id"] for c in options["allowCredentials"]] == [
            authenticator.credential_id_b64
        ]
        assert len(b64url_decode(options["challenge"])) >= 16

    def test_a_genuine_assertion_completes_sign_in_at_aal2_under_a_new_token(
        self, harness, authenticator
    ):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        pending_token = browser.cookies["__Host-session"]

        response = _passkey_sign_in(harness, authenticator, browser)

        assert response.status_code == 200
        assert response.json()["status"] == "signed_in"
        assert session_cookie_value(response) != pending_token
        assert _session_aal(browser) == 2

    @pytest.mark.parametrize(
        "forgery",
        [
            pytest.param({"tamper_signature": True}, id="bad-signature"),
            pytest.param({"origin": "https://evil.example"}, id="another-origin"),
            pytest.param({"rp_id": "evil.example"}, id="another-relying-party"),
            pytest.param({"user_verified": False}, id="no-user-verification"),
            pytest.param(
                {"ceremony_type": "webauthn.create"}, id="a-creation-not-an-assertion"
            ),
            pytest.param(
                {"challenge": "bm90LWlzc3VlZC1ieS10aGUtc2VydmVy"}, id="unissued-challenge"
            ),
        ],
    )
    def test_a_forged_assertion_is_refused(self, harness, authenticator, forgery):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)

        response = _passkey_sign_in(harness, authenticator, browser, **forgery)

        assert response.status_code == 401
        assert browser.get("/api/v1/auth/session").status_code == 401

    def test_another_users_passkey_is_refused(self, harness, authenticator):
        stranger = _user_with_passkey(harness, "kofi@example.org", NEW_PASSWORD)
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        options = post(browser, "/auth/mfa/passkey/options").json()["options"]

        response = post(
            browser, "/auth/mfa/passkey", {"credential": stranger.get(options, origin=ORIGIN)}
        )

        assert response.status_code == 401

    def test_a_challenge_is_spent_by_any_attempt_even_a_failed_one(
        self, harness, authenticator
    ):
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        options = post(browser, "/auth/mfa/passkey/options").json()["options"]
        forged = authenticator.get(options, origin=ORIGIN, tamper_signature=True)
        honest = authenticator.get(options, origin=ORIGIN)

        post(browser, "/auth/mfa/passkey", {"credential": forged})
        response = post(browser, "/auth/mfa/passkey", {"credential": honest})

        assert response.status_code == 401

    def test_a_captured_assertion_cannot_be_replayed_in_a_new_sign_in(
        self, harness, authenticator
    ):
        first = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        options = post(first, "/auth/mfa/passkey/options").json()["options"]
        captured = authenticator.get(options, origin=ORIGIN)
        post(first, "/auth/mfa/passkey", {"credential": captured})

        second = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        post(second, "/auth/mfa/passkey/options")
        response = post(second, "/auth/mfa/passkey", {"credential": captured})

        assert response.status_code == 401

    def test_a_sign_count_that_goes_backwards_is_refused(self, harness, authenticator):
        """The signature of a cloned authenticator: WebAuthn §6.1.1."""
        _passkey_sign_in(
            harness,
            authenticator,
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            sign_count=5,
        )

        response = _passkey_sign_in(
            harness,
            authenticator,
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            sign_count=3,
        )

        assert response.status_code == 401

    def test_a_cloned_authenticator_is_caught_by_its_counter(self, harness, authenticator):
        clone = authenticator.clone()
        _passkey_sign_in(
            harness, authenticator, sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        )
        _passkey_sign_in(
            harness, authenticator, sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        )

        response = _passkey_sign_in(
            harness, clone, sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        )

        assert response.status_code == 401

    def test_authenticators_that_never_count_can_sign_in_repeatedly(self, harness):
        harness.create_user("kofi@example.org", NEW_PASSWORD)
        silent = SoftwareAuthenticator(counts_signatures=False)
        register_passkey(
            harness, _signed_in_without_mfa(harness, "kofi@example.org", NEW_PASSWORD), silent
        )

        outcomes = [
            _passkey_sign_in(
                harness, silent, sign_in_pending(harness, "kofi@example.org", NEW_PASSWORD)
            ).status_code
            for _ in range(2)
        ]

        assert outcomes == [200, 200]


class TestRemovingAuthenticators:
    @pytest.fixture
    def secret(self, harness: AuthHarness) -> str:
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        return secret

    def _with_passkey(self, harness: AuthHarness, secret: str) -> tuple[TestClient, str]:
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)
        response = register_passkey(harness, browser, SoftwareAuthenticator())
        assert response.status_code == 201, response.text
        passkey_id: str = response.json()["passkey"]["id"]
        return browser, passkey_id

    def test_the_last_authenticator_cannot_be_removed(self, harness, secret):
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)

        response = delete(browser, "/account/mfa/totp")

        assert response.status_code == 409
        assert _mfa_status(browser)["totp"] is not None

    def test_one_of_two_authenticators_can_be_removed_but_then_not_the_other(
        self, harness, secret
    ):
        browser, passkey_id = self._with_passkey(harness, secret)

        removed = delete(browser, "/account/mfa/totp")
        last = delete(browser, f"/account/mfa/passkeys/{passkey_id}")

        assert removed.status_code == 204
        assert last.status_code == 409
        assert len(_mfa_status(browser)["passkeys"]) == 1

    def test_a_removed_totp_no_longer_signs_in(self, harness, secret):
        browser, _ = self._with_passkey(harness, secret)
        delete(browser, "/account/mfa/totp")

        pending = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        response = post(pending, "/auth/mfa/totp", {"code": next_totp(harness, secret)})

        assert response.status_code == 401
        assert (
            "totp" not in login(harness.browser(), USER_EMAIL, USER_PASSWORD).json()["methods"]
        )

    def test_removing_needs_a_recent_sign_in(self, harness, secret):
        browser, passkey_id = self._with_passkey(harness, secret)
        harness.clock.advance(minutes=16)
        browser.get("/api/v1/auth/session")

        response = delete(browser, f"/account/mfa/passkeys/{passkey_id}")

        assert response.status_code == 401
        assert response.json()["reauthentication_required"] is True

    def test_another_users_passkey_cannot_be_removed_or_even_seen(self, harness, secret):
        _, passkey_id = self._with_passkey(harness, secret)
        stranger_secret, _ = user_with_totp(harness, "kofi@example.org", NEW_PASSWORD)
        stranger = sign_in_with_totp(harness, "kofi@example.org", NEW_PASSWORD, stranger_secret)

        response = delete(stranger, f"/account/mfa/passkeys/{passkey_id}")

        assert response.status_code == 404

    def test_the_user_is_emailed_when_an_authenticator_is_removed(self, harness, secret):
        browser, _ = self._with_passkey(harness, secret)

        delete(browser, "/account/mfa/totp")

        message = harness.outbox[-1]
        assert message.to == USER_EMAIL  # type: ignore[attr-defined]
        assert "removed" in message.subject.lower()  # type: ignore[attr-defined]
        assert set(harness.email.open_units_seen) == {0}

    async def test_removing_the_last_two_at_once_leaves_one(self, harness, secret):
        """Check-then-delete in two requests would otherwise leave the account with none."""
        browser, passkey_id = self._with_passkey(harness, secret)

        statuses = await _concurrently(
            harness,
            [
                ("DELETE", "/account/mfa/totp", browser, None),
                ("DELETE", f"/account/mfa/passkeys/{passkey_id}", browser, None),
            ],
        )

        assert sorted(statuses) == [204, 409]
        status = _mfa_status(browser)
        assert (status["totp"] is not None) + len(status["passkeys"]) == 1


class TestPasswordResetDoesNotBypassMfa:
    def test_after_a_reset_sign_in_still_asks_for_the_second_factor(self, harness):
        user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})
        confirm = harness.browser().post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": last_link(harness, "/reset-password"), "new_password": NEW_PASSWORD},
        )

        response = login(harness.browser(), USER_EMAIL, NEW_PASSWORD)

        assert "__Host-session" not in set_cookie_headers(confirm)
        assert response.json()["status"] == "mfa_required"

    def test_a_reset_leaves_every_authenticator_in_place(self, harness):
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        harness.browser().post("/api/v1/auth/password-reset", json={"email": USER_EMAIL})
        harness.browser().post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": last_link(harness, "/reset-password"), "new_password": NEW_PASSWORD},
        )

        browser = sign_in_with_totp(harness, USER_EMAIL, NEW_PASSWORD, secret)

        assert _mfa_status(browser)["totp"] is not None


class TestMfaAuditTrail:
    def test_every_change_to_authenticators_is_recorded(self, harness):
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        browser = sign_in_with_totp(harness, USER_EMAIL, USER_PASSWORD, secret)
        passkey_id = register_passkey(harness, browser, SoftwareAuthenticator()).json()[
            "passkey"
        ]["id"]
        post(browser, "/account/mfa/recovery-codes")
        delete(browser, "/account/mfa/totp")
        enrol_totp(harness, browser)
        delete(browser, f"/account/mfa/passkeys/{passkey_id}")

        actions = _audit_actions(harness)

        for action in (
            "account.totp_added",
            "account.passkey_added",
            "account.recovery_codes_generated",
            "account.totp_removed",
            "account.passkey_removed",
        ):
            assert action in actions, action

    def test_each_step_of_a_two_step_sign_in_is_recorded(self, harness):
        secret, codes = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        post(browser, "/auth/mfa/totp", {"code": _wrong_code(secret, harness)})
        post(browser, "/auth/mfa/totp", {"code": next_totp(harness, secret)})
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/recovery-code",
            {"code": codes[0]},
        )

        actions = _audit_actions(harness)

        assert actions[-5:] == [
            "auth.login_pending_mfa",
            "auth.mfa_failed",
            "auth.mfa_totp_succeeded",
            "auth.login_pending_mfa",
            "auth.mfa_recovery_code_succeeded",
        ]

    def test_passkey_sign_ins_are_recorded(self, harness):
        authenticator = _user_with_passkey(harness)

        _passkey_sign_in(
            harness, authenticator, sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        )

        assert _audit_actions(harness)[-1] == "auth.mfa_passkey_succeeded"

    def test_audit_records_hold_no_secret_code_or_email(self, harness):
        secret, codes = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        code = next_totp(harness, secret)
        post(
            sign_in_pending(harness, USER_EMAIL, USER_PASSWORD),
            "/auth/mfa/totp",
            {"code": code},
        )

        events = repr(harness.storage.state.audit_events)

        assert secret not in events
        assert code not in events
        assert not any(c in events or c.replace("-", "") in events for c in codes)
        assert "example.org" not in events


class TestRateLimiterOutage:
    """Gap G1: when Valkey is down, sign-in fails closed — 503, never 500, never unthrottled."""

    def test_sign_in_answers_503_with_retry_after_when_valkey_is_unreachable(self):
        # Port 1 on loopback refuses connections: a real outage, not a simulated one.
        harness = make_harness(valkey_url="redis://127.0.0.1:1/0")
        harness.create_user(USER_EMAIL, USER_PASSWORD)
        before = harness.hasher.verify_calls

        response = login(harness.browser(), USER_EMAIL, USER_PASSWORD)

        assert response.status_code == 503
        assert response.headers["content-type"] == "application/problem+json"
        assert int(response.headers["retry-after"]) > 0
        assert harness.hasher.verify_calls == before  # no un-throttled password check

    async def test_the_valkey_limiter_reports_an_outage_as_its_own_error_and_quickly(self):
        """Not `redis.ConnectionError`: the domain error is what the API maps to 503."""
        from app.domain.errors import RateLimiterUnavailableError
        from app.ratelimit.valkey import ValkeyRateLimiter

        limiter = ValkeyRateLimiter.from_url("redis://127.0.0.1:1/0")
        try:
            with pytest.raises(RateLimiterUnavailableError):
                await asyncio.wait_for(limiter.hit("login:x", 5, timedelta(minutes=15)), 10)
        finally:
            await limiter.close()

    def test_a_second_factor_answers_503_and_the_code_stays_unspent(self):
        from app.ratelimit.memory import MemoryRateLimiter

        clock = FakeClock()
        limiter = SwitchableRateLimiter(MemoryRateLimiter(clock))
        harness = make_harness(rate_limiter=limiter, clock=clock)
        secret, _ = user_with_totp(harness, USER_EMAIL, USER_PASSWORD)
        browser = sign_in_pending(harness, USER_EMAIL, USER_PASSWORD)
        code = next_totp(harness, secret)

        limiter.is_down = True
        outage = post(browser, "/auth/mfa/totp", {"code": code})
        limiter.is_down = False
        recovered = post(browser, "/auth/mfa/totp", {"code": code})

        assert outage.status_code == 503
        assert int(outage.headers["retry-after"]) > 0
        assert recovered.status_code == 200

    def test_a_reset_request_answers_503_and_sends_nothing(self):
        from app.ratelimit.memory import MemoryRateLimiter

        clock = FakeClock()
        limiter = SwitchableRateLimiter(MemoryRateLimiter(clock))
        harness = make_harness(rate_limiter=limiter, clock=clock)
        harness.create_user(USER_EMAIL, USER_PASSWORD)

        limiter.is_down = True
        response = harness.browser().post(
            "/api/v1/auth/password-reset", json={"email": USER_EMAIL}
        )

        assert response.status_code == 503
        assert harness.outbox == []
