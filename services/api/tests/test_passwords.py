"""Requirements for passwords: what is accepted, and how they are stored.

Source: NIST SP 800-63B rev 4 (password verifiers); OWASP ASVS 5.0 V6
(Authentication); WEB_APP_BRIEF rule 6.
"""

from __future__ import annotations

import pytest

from app.auth.argon2 import Argon2PasswordHasher
from app.auth.fake import FakePasswordHasher
from app.domain.passwords import PasswordPolicy, normalize_password

POLICY = PasswordPolicy()


class TestPasswordPolicy:
    """Length and screening only. No composition rules, no forced rotation (NIST)."""

    def test_a_long_passphrase_is_accepted(self):
        assert POLICY.violations("seventeen-copper-kites-over-lagos") == ()

    def test_fifteen_characters_is_the_minimum(self):
        assert POLICY.violations("a7#kP2!mQ9z@Lw4") == ()
        assert "too_short" in POLICY.violations("a7#kP2!mQ9z@Lw")

    def test_no_composition_rules_lowercase_words_alone_are_fine(self):
        assert POLICY.violations("quiet river under amber lanterns") == ()

    def test_spaces_and_unicode_are_allowed(self):
        assert POLICY.violations("ọ̀rọ̀ àṣírí mi kò ṣeé gbọ́") == ()
        assert POLICY.violations("🔒🌍🔑🌊🔥🌱⭐🎈🧭🪁🎲🧩📚🕯️🛶") == ()

    def test_length_is_counted_in_characters_not_bytes(self):
        assert "too_short" in POLICY.violations("ééééééééééééé")  # 13 chars, 26 bytes

    def test_an_absurdly_long_password_is_refused_to_protect_the_hasher(self):
        assert "too_long" in POLICY.violations("x" * 1025)
        assert POLICY.violations("long enough " * 85) == ()  # 1020 characters

    @pytest.mark.parametrize(
        "common",
        ["passwordpassword", "123456789012345", "qwertyuiopasdfgh", "iloveyouiloveyou"],
    )
    def test_common_passwords_are_refused(self, common):
        assert "too_common" in POLICY.violations(common)

    def test_a_single_repeated_character_is_refused(self):
        assert "too_common" in POLICY.violations("aaaaaaaaaaaaaaaaaaaa")

    def test_passwords_built_from_the_users_own_details_are_refused(self):
        violations = POLICY.violations(
            "amara.okafor.2026!", context=("amara.okafor@example.org", "Amara Okafor")
        )

        assert "contains_personal_details" in violations

    def test_violations_are_reported_together(self):
        assert set(POLICY.violations("password")) == {"too_short", "too_common"}


class TestNormalisation:
    def test_equivalent_unicode_forms_normalise_to_the_same_password(self):
        composed = "café-on-the-corner-at-dawn"
        decomposed = "café-on-the-corner-at-dawn"

        assert normalize_password(composed) == normalize_password(decomposed)


class TestArgon2PasswordHasher:
    """Argon2id, the current OWASP recommendation, with upgrade-on-login."""

    def test_a_hash_verifies_only_its_own_password(self):
        hasher = Argon2PasswordHasher()
        stored = hasher.hash("seventeen-copper-kites-over-lagos")

        assert hasher.verify(stored, "seventeen-copper-kites-over-lagos")
        assert not hasher.verify(stored, "seventeen-copper-kites-over-accra")

    def test_hashes_are_argon2id_and_salted(self):
        hasher = Argon2PasswordHasher()
        first = hasher.hash("same-password-twice-over")
        second = hasher.hash("same-password-twice-over")

        assert first.startswith("$argon2id$")
        assert first != second

    def test_a_hash_made_with_weaker_parameters_needs_rehashing(self):
        weak = Argon2PasswordHasher(memory_cost_kib=8 * 1024, time_cost=1)
        current = Argon2PasswordHasher()

        assert current.needs_rehash(weak.hash("seventeen-copper-kites-over-lagos"))
        assert not current.needs_rehash(current.hash("seventeen-copper-kites-over-lagos"))

    def test_a_malformed_hash_does_not_verify_and_does_not_crash(self):
        assert not Argon2PasswordHasher().verify("not-a-hash", "anything-at-all-here")


class TestFakePasswordHasher:
    def test_it_behaves_like_a_hasher_and_counts_verifications(self):
        hasher = FakePasswordHasher()
        stored = hasher.hash("pw-for-tests-only-123")

        assert hasher.verify(stored, "pw-for-tests-only-123")
        assert not hasher.verify(stored, "wrong")
        assert hasher.verify_calls == 2
