"""Requirements for the rules of multi-factor authentication, without I/O.

Covers TOTP (RFC 6238), recovery codes, and WebAuthn sign counters and challenges.
`app.domain.mfa` uses the standard library only; the WebAuthn ceremony itself is verified
by a provider and specified in `test_mfa_api.py`.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.mfa import (
    RECOVERY_CODE_COUNT,
    WEBAUTHN_CHALLENGE_LIFETIME,
    ChallengePurpose,
    MfaEnrolment,
    MfaPolicy,
    Passkey,
    RecoveryCode,
    TotpCredential,
    TotpPolicy,
    WebAuthnChallenge,
    generate_recovery_codes,
    matching_totp_step,
    new_totp_secret,
    new_webauthn_challenge,
    normalize_recovery_code,
    recovery_code_digest,
    totp_code,
    totp_provisioning_uri,
    totp_step,
)
from app.domain.sessions import Session, SessionPolicy, hash_token, new_token

START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
# The shared secret of RFC 6238 Appendix B for HMAC-SHA1.
RFC_SECRET = b"12345678901234567890"
POLICY = TotpPolicy()
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_FULLWIDTH_123456 = "".join(chr(0xFF10 + digit) for digit in range(1, 7))


def _at(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(unix_seconds, UTC)


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


def _passkey(sign_count: int) -> Passkey:
    return Passkey(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credential_id=b"credential-id-bytes",
        public_key=b"cose-public-key",
        sign_count=sign_count,
        name="Laptop",
        created_at=START,
    )


def _challenge(**overrides: object) -> WebAuthnChallenge:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "purpose": ChallengePurpose.AUTHENTICATION,
        "challenge": new_webauthn_challenge(),
        "created_at": START,
        "expires_at": START + WEBAUTHN_CHALLENGE_LIFETIME,
    }
    values.update(overrides)
    return WebAuthnChallenge(**values)  # type: ignore[arg-type]


class TestTotpPolicy:
    def test_defaults_are_what_every_authenticator_app_assumes(self):
        """RFC 6238 defaults: anything else silently breaks Google Authenticator & co."""
        assert POLICY.algorithm == "SHA1"
        assert POLICY.digits == 6
        assert POLICY.step == timedelta(seconds=30)

    def test_clock_drift_of_one_step_either_way_is_tolerated_and_no_more(self):
        assert POLICY.allowed_drift_steps == 1


class TestTotpCodes:
    @pytest.mark.parametrize(
        ("unix_seconds", "expected"),
        [
            # RFC 6238 Appendix B, SHA-1 column, reduced from 8 to 6 digits.
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
            (20000000000, "353130"),
        ],
    )
    def test_codes_match_the_rfc_6238_test_vectors(self, unix_seconds, expected):
        assert totp_code(RFC_SECRET, _at(unix_seconds), POLICY) == expected

    def test_codes_keep_their_leading_zeros(self):
        assert totp_code(RFC_SECRET, _at(1234567890), POLICY) == "005924"

    def test_the_time_step_counts_30_second_intervals_from_the_unix_epoch(self):
        assert totp_step(_at(59), POLICY) == 1
        assert totp_step(_at(60), POLICY) == 2

    def test_secrets_are_160_bits_from_the_csprng_and_never_repeat(self):
        """RFC 4226 section 4 recommends 160 bits, the length of the SHA-1 output."""
        secrets = {new_totp_secret() for _ in range(1000)}

        assert len(secrets) == 1000
        assert all(len(s) == 20 for s in secrets)


class TestTotpVerification:
    def _step(self, at: datetime) -> int:
        return totp_step(at, POLICY)

    def test_the_current_code_is_accepted_and_reports_its_step(self):
        code = totp_code(RFC_SECRET, START, POLICY)

        step = matching_totp_step(RFC_SECRET, code, START, last_used_step=None, policy=POLICY)

        assert step == self._step(START)

    @pytest.mark.parametrize("drift", [-1, 1])
    def test_a_code_one_step_early_or_late_is_accepted(self, drift):
        code = totp_code(RFC_SECRET, START + drift * POLICY.step, POLICY)

        step = matching_totp_step(RFC_SECRET, code, START, last_used_step=None, policy=POLICY)

        assert step == self._step(START) + drift

    @pytest.mark.parametrize("drift", [-2, 2])
    def test_a_code_two_steps_away_is_refused(self, drift):
        code = totp_code(RFC_SECRET, START + drift * POLICY.step, POLICY)

        assert (
            matching_totp_step(RFC_SECRET, code, START, last_used_step=None, policy=POLICY)
            is None
        )

    def test_a_code_whose_step_was_already_used_is_refused(self):
        """Replay: NIST 800-63B requires an OTP to be accepted only once."""
        code = totp_code(RFC_SECRET, START, POLICY)

        assert (
            matching_totp_step(
                RFC_SECRET, code, START, last_used_step=self._step(START), policy=POLICY
            )
            is None
        )

    def test_an_older_code_is_refused_once_a_newer_one_was_used(self):
        """Steps only move forward, so a code captured earlier can never be replayed."""
        older = totp_code(RFC_SECRET, START - POLICY.step, POLICY)

        assert (
            matching_totp_step(
                RFC_SECRET, older, START, last_used_step=self._step(START), policy=POLICY
            )
            is None
        )

    def test_a_newer_code_is_accepted_after_an_older_one_was_used(self):
        newer = totp_code(RFC_SECRET, START + POLICY.step, POLICY)

        step = matching_totp_step(
            RFC_SECRET, newer, START, last_used_step=self._step(START), policy=POLICY
        )

        assert step == self._step(START) + 1

    @pytest.mark.parametrize(
        "code",
        # The last is fullwidth digits: `str.isdigit()` accepts them, and must not.
        ["", "12345", "1234567", "12345a", " 123456", _FULLWIDTH_123456],
    )
    def test_anything_but_exactly_six_ascii_digits_is_refused(self, code):
        assert (
            matching_totp_step(RFC_SECRET, code, START, last_used_step=None, policy=POLICY)
            is None
        )

    def test_a_code_for_another_secret_is_refused(self):
        code = totp_code(new_totp_secret(), START, POLICY)

        # 1 in 10^6 per candidate step: with three candidate steps this could
        # collide once in ~333,000 runs. Accepted for a spec this direct.
        assert (
            matching_totp_step(RFC_SECRET, code, START, last_used_step=None, policy=POLICY)
            is None
        )


class TestProvisioningUri:
    def _uri(self) -> str:
        return totp_provisioning_uri(
            RFC_SECRET, account_name="amara@example.org", issuer="humani-t", policy=POLICY
        )

    def test_the_uri_is_the_key_uri_format_authenticator_apps_scan(self):
        parts = urlsplit(self._uri())

        assert parts.scheme == "otpauth"
        assert parts.netloc == "totp"
        assert parts.path == "/humani-t:amara%40example.org"

    def test_the_uri_carries_the_secret_issuer_and_parameters(self):
        query = parse_qs(urlsplit(self._uri()).query)

        assert query["secret"] == ["GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"]
        assert query["issuer"] == ["humani-t"]
        assert query["algorithm"] == ["SHA1"]
        assert query["digits"] == ["6"]
        assert query["period"] == ["30"]


class TestTotpCredential:
    def _credential(self, **overrides: object) -> TotpCredential:
        values: dict[str, object] = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "secret_sealed": b"ciphertext",
            "created_at": START,
        }
        values.update(overrides)
        return TotpCredential(**values)  # type: ignore[arg-type]

    def test_a_new_credential_is_not_confirmed_until_a_code_proves_it(self):
        credential = self._credential()

        assert not credential.is_confirmed
        assert credential.confirmed(START).is_confirmed

    def test_the_secret_is_held_only_sealed(self):
        """The entity has no plaintext field: there is nowhere to store it by mistake."""
        field_names = set(TotpCredential.__dataclass_fields__)

        assert "secret_sealed" in field_names
        assert "secret" not in field_names

    def test_a_negative_last_used_step_is_rejected(self):
        with pytest.raises(ValueError, match="last_used_step"):
            self._credential(last_used_step=-1)

    def test_timestamps_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            self._credential(created_at=datetime(2026, 1, 1))  # noqa: DTZ001 — the invalid input


class TestRecoveryCodes:
    def test_a_set_is_ten_distinct_codes(self):
        codes = generate_recovery_codes()

        assert RECOVERY_CODE_COUNT == 10
        assert len(codes) == 10
        assert len(set(codes)) == 10

    def test_codes_are_four_groups_of_four_unambiguous_characters(self):
        """Crockford base32: no I, L, O or U to misread when typing from paper."""
        pattern = re.compile(rf"^[{_CROCKFORD}]{{4}}(-[{_CROCKFORD}]{{4}}){{3}}$")

        assert all(pattern.match(code) for code in generate_recovery_codes())

    def test_each_code_carries_at_least_64_bits_of_entropy(self):
        """NIST 800-63B look-up secrets: at least 64 bits from an approved RNG."""
        code = generate_recovery_codes()[0]
        symbols = code.replace("-", "")

        bits = len(symbols) * 5  # log2(32) bits per Crockford symbol
        assert bits >= 64

    def test_codes_never_repeat_across_sets(self):
        seen = [code for _ in range(500) for code in generate_recovery_codes()]

        assert len(set(seen)) == len(seen)

    @pytest.mark.parametrize(
        "typed",
        [
            "abcd-efgh-jkmn-pq01",
            "ABCDEFGHJKMNPQ01",
            " abcd efgh jkmn pq01 ",
            "ABCD-EFGH-JKMN-PQOI",
        ],
    )
    def test_typing_variants_normalise_to_the_same_code(self, typed):
        """Case, spacing, dashes, and O/I/L typed for 0/1 all mean the same code."""
        assert normalize_recovery_code(typed) == "ABCDEFGHJKMNPQ01"

    def test_the_digest_is_keyed_so_a_database_copy_alone_cannot_check_guesses(self):
        code = "ABCD-EFGH-JKMN-PQ01"
        key = b"k" * 32

        digest = recovery_code_digest(key, code)

        assert digest != hashlib.sha256(normalize_recovery_code(code).encode()).digest()
        assert digest != recovery_code_digest(b"x" * 32, code)
        assert len(digest) == 32

    def test_the_digest_is_the_same_for_every_way_of_typing_the_code(self):
        key = b"k" * 32

        assert recovery_code_digest(key, "abcd efgh jkmn pq01") == recovery_code_digest(
            key,
            "ABCD-EFGH-JKMN-PQ01",  # gitleaks:allow
        )

    def test_a_used_code_is_not_usable(self):
        code = RecoveryCode(
            id=uuid.uuid4(), user_id=uuid.uuid4(), code_hash=b"h" * 32, created_at=START
        )

        assert code.is_usable
        assert not code.used(START).is_usable


class TestPasskeySignCount:
    """WebAuthn §6.1.1: a counter that does not advance signals a cloned authenticator."""

    def test_a_higher_count_is_accepted(self):
        assert _passkey(sign_count=5).accepts_sign_count(6)

    @pytest.mark.parametrize("presented", [5, 4, 0])
    def test_a_count_that_does_not_advance_is_refused(self, presented):
        assert not _passkey(sign_count=5).accepts_sign_count(presented)

    def test_authenticators_that_always_report_zero_are_accepted(self):
        """Many platform passkeys do not count at all; the spec says to allow them."""
        assert _passkey(sign_count=0).accepts_sign_count(0)

    def test_a_passkey_name_must_not_be_blank(self):
        with pytest.raises(ValueError, match=r"Passkey\.name"):
            Passkey(
                id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=b"id",
                public_key=b"key",
                sign_count=0,
                name="  ",
                created_at=START,
            )


class TestWebAuthnChallenge:
    def test_challenges_are_at_least_16_random_bytes_and_never_repeat(self):
        """WebAuthn §13.4.3 requires at least 16 bytes; 32 leaves no doubt."""
        challenges = {new_webauthn_challenge() for _ in range(1000)}

        assert len(challenges) == 1000
        assert all(len(c) >= 16 for c in challenges)

    def test_a_challenge_lives_five_minutes(self):
        challenge = _challenge()

        assert timedelta(minutes=5) == WEBAUTHN_CHALLENGE_LIFETIME
        assert challenge.is_usable(START + timedelta(minutes=4, seconds=59))
        assert not challenge.is_usable(START + timedelta(minutes=5, seconds=1))

    def test_a_spent_challenge_is_never_usable_again(self):
        assert not _challenge().used(START).is_usable(START)

    def test_the_two_ceremonies_have_distinct_purposes(self):
        assert {p.value for p in ChallengePurpose} == {"registration", "authentication"}


class TestMfaPolicy:
    def test_mfa_is_required_unless_explicitly_turned_off(self):
        assert MfaPolicy().is_required is True


class TestMfaEnrolment:
    def test_an_account_with_no_authenticator_is_not_enrolled(self):
        assert not MfaEnrolment(has_totp=False, passkey_count=0).is_enrolled

    @pytest.mark.parametrize(("has_totp", "passkeys"), [(True, 0), (False, 1), (True, 2)])
    def test_any_authenticator_counts_as_enrolled(self, has_totp, passkeys):
        assert MfaEnrolment(has_totp=has_totp, passkey_count=passkeys).is_enrolled

    def test_the_last_authenticator_cannot_be_removed(self):
        assert not MfaEnrolment(has_totp=True, passkey_count=0).can_remove_an_authenticator
        assert not MfaEnrolment(has_totp=False, passkey_count=1).can_remove_an_authenticator

    def test_one_of_two_authenticators_can_be_removed(self):
        assert MfaEnrolment(has_totp=True, passkey_count=1).can_remove_an_authenticator
        assert MfaEnrolment(has_totp=False, passkey_count=2).can_remove_an_authenticator

    def test_counts_cannot_be_negative(self):
        with pytest.raises(ValueError, match="passkey_count"):
            MfaEnrolment(has_totp=False, passkey_count=-1)


class TestSessionAssurance:
    def test_a_session_starts_at_aal1_and_not_pending_unless_stated(self):
        """The defaults keep every session created before M9 at the lower level."""
        session = _session()

        assert session.aal == 1
        assert session.is_mfa_pending is False

    @pytest.mark.parametrize("aal", [0, 3])
    def test_only_aal1_and_aal2_exist(self, aal):
        with pytest.raises(ValueError, match="aal"):
            _session(aal=aal)

    def test_a_session_waiting_for_mfa_cannot_claim_aal2(self):
        with pytest.raises(ValueError, match="is_mfa_pending"):
            _session(aal=2, is_mfa_pending=True)

    def test_a_pending_session_lasts_five_minutes_however_active(self):
        policy = SessionPolicy()
        session = _session(is_mfa_pending=True).touched(START + timedelta(minutes=4))

        assert policy.pending_mfa_timeout == timedelta(minutes=5)
        assert session.is_active(START + timedelta(minutes=4, seconds=59), policy)
        assert not session.is_active(START + timedelta(minutes=5, seconds=1), policy)

    def test_a_full_session_keeps_the_aal2_lifetimes(self):
        policy = SessionPolicy()
        session = _session(aal=2).touched(START + timedelta(minutes=50))

        assert session.is_active(START + timedelta(minutes=100), policy)
