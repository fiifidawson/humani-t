"""The always-on pattern detector: structured identifiers, found with the standard library.

Sources: the brief's rule 5 (medical record numbers such as `MRN 448-1207` were
missed), rule 3 (medical record and insurance ids, phone numbers, emails) and
architecture §3.9.1 (pattern rules for structured identifiers).

Every pattern has a table of texts it must find and texts it must leave alone.
The negatives matter as much as the positives: the same detector checks every
outgoing prompt, and a pattern that fires on "95% CI 1.2-3.4" or a year range
would refuse ordinary screening prompts about published papers.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain.masking import EntityType, MaskSpan
from app.privacy.patterns import PatternDetector

DETECTOR = PatternDetector()
_PRIME, _DOUBLE_PRIME = chr(0x2032), chr(0x2033)


def found(text: str) -> list[tuple[EntityType, str]]:
    spans: tuple[MaskSpan, ...] = asyncio.run(DETECTOR.detect(text))
    return [(s.entity_type, text[s.start : s.end]) for s in spans]


def types_in(text: str) -> set[EntityType]:
    return {kind for kind, _ in found(text)}


POSITIVES: list[tuple[EntityType, str, str]] = [
    # Emails, including the obfuscated forms papers use for corresponding authors.
    (EntityType.EMAIL, "Write to amina.yusuf@example.org today", "amina.yusuf@example.org"),
    (EntityType.EMAIL, "contact: k.mensah+field@who.int.", "k.mensah+field@who.int"),
    (EntityType.EMAIL, "Email: amina [at] example [dot] org", "amina [at] example [dot] org"),
    (EntityType.EMAIL, "kofi(at)msf.org for data", "kofi(at)msf.org"),
    # Phone numbers: international, local with an area code, labelled.
    (EntityType.PHONE, "Call +254 712 345 678 now", "+254 712 345 678"),
    (EntityType.PHONE, "WhatsApp +967-1-234-5678.", "+967-1-234-5678"),
    (EntityType.PHONE, "on +41 (0)22 730 21 11 or", "+41 (0)22 730 21 11"),
    (EntityType.PHONE, "dial 0041 22 730 21 11 today", "0041 22 730 21 11"),
    (EntityType.PHONE, "Tel: 0712 345 678", "0712 345 678"),
    (EntityType.PHONE, "mobile 0712345678", "0712345678"),
    (EntityType.PHONE, "phone (020) 7946 0958 please", "(020) 7946 0958"),
    (EntityType.PHONE, "US line 555-123-4567.", "555-123-4567"),
    # Medical record numbers: brief rule 5's example and its variants.
    (EntityType.MEDICAL_RECORD, "MRN 448-1207 was referred", "448-1207"),
    (EntityType.MEDICAL_RECORD, "MRN: 4481207", "4481207"),
    (EntityType.MEDICAL_RECORD, "mrn#448-1207", "448-1207"),
    (EntityType.MEDICAL_RECORD, "MR No. 448/1207 seen", "448/1207"),
    (EntityType.MEDICAL_RECORD, "medical record number 00448 1207.", "00448 1207"),
    (EntityType.MEDICAL_RECORD, "Patient ID: KX-20931", "KX-20931"),
    (EntityType.MEDICAL_RECORD, "hospital no. 77-4410", "77-4410"),
    # National-ID-like numbers: labelled, and two well-known shapes.
    (EntityType.ID_NUMBER, "national ID no. 19876543", "19876543"),
    (EntityType.ID_NUMBER, "NIN: CM12345678ABCD", "CM12345678ABCD"),
    (EntityType.ID_NUMBER, "SSN 123-45-6789", "123-45-6789"),
    (EntityType.ID_NUMBER, "CNIC 35202-1234567-1 holder", "35202-1234567-1"),
    (EntityType.ID_NUMBER, "UNHCR case no. 123-12C45678", "123-12C45678"),
    (EntityType.ID_NUMBER, "proGres ID 845-00123456", "845-00123456"),
    # Passport-like numbers, only next to the word.
    (EntityType.PASSPORT, "passport no. N1234567", "N1234567"),
    (EntityType.PASSPORT, "Passport: 12AB34567", "12AB34567"),
    # Bank and card numbers, only when their check digits are right.
    (EntityType.IBAN, "IBAN CH93 0076 2011 6238 5295 7 for", "CH93 0076 2011 6238 5295 7"),
    (EntityType.IBAN, "to GB82WEST12345698765432.", "GB82WEST12345698765432"),
    (EntityType.CARD_NUMBER, "card 4111 1111 1111 1111 exp", "4111 1111 1111 1111"),
    (EntityType.CARD_NUMBER, "paid with 5500-0000-0000-0004", "5500-0000-0000-0004"),
    # Addresses of machines.
    (EntityType.IP_ADDRESS, "from 192.168.10.24 at", "192.168.10.24"),
    (EntityType.IP_ADDRESS, "host 2001:db8::8a2e:370:7334 down", "2001:db8::8a2e:370:7334"),
    # Coordinates: decimal pairs, hemispheres, degrees and minutes.
    (EntityType.COORDINATES, "camp at 15.3694, 44.1910 near", "15.3694, 44.1910"),
    (EntityType.COORDINATES, "GPS -1.2921,36.8219.", "-1.2921,36.8219"),
    (EntityType.COORDINATES, "at 15.3694° N, 44.1910° E", "15.3694° N, 44.1910° E"),
    (EntityType.COORDINATES, "site 15°22'N 44°11'E", "15°22'N 44°11'E"),
    (
        EntityType.COORDINATES,
        f"at 2°02{_PRIME}31{_DOUBLE_PRIME}S 45°20{_PRIME}E today",
        f"2°02{_PRIME}31{_DOUBLE_PRIME}S 45°20{_PRIME}E",
    ),
]

NEGATIVES: list[str] = [
    # Ordinary statistics in abstracts.
    "Mortality fell (95% CI 1.2-3.4, p < 0.001) between 2019-2023.",
    "Risk ratio 0.82 (0.71, 0.95); n = 1,234,567 households.",
    "Effect sizes were 0.12, 0.45 and 0.78 respectively.",
    # Identifiers of papers, not people.
    "doi:10.1371/journal.pone.0123456 and PMID 31415926",
    "ISBN 978-3-16-148410-0, arXiv:2301.01234v2",
    # Dates and times.
    "Seen on 12/03/2021 at 10:30:45, follow-up 2021-03-12.",
    # Bare numbers that are not labelled as an identifier.
    "Case 3 of 10; ward 12; 448 patients; 1207 visits.",
    "In 2020, 4481207 people were displaced.",
    # Near misses: bad check digits, impossible addresses and coordinates.
    "card 4111 1111 1111 1112 failed the check",
    "IBAN GB82WEST12345698765433 is wrong",
    "version 999.168.10.24 is not an address",
    "Latitude 95.12345, 200.12345 is not on Earth.",
    # Social handles and placeholders are not emails or phones.
    "Follow @who_africa and see [EMAIL_1] or [PHONE_2].",
    "the word passport alone, or passports issued",
]


class TestPatternDetector:
    def test_the_source_names_the_detector_and_its_version(self):
        assert DETECTOR.source == "patterns@1"

    def test_it_covers_exactly_the_structured_identifiers(self):
        assert DETECTOR.entity_types == {
            EntityType.EMAIL,
            EntityType.PHONE,
            EntityType.MEDICAL_RECORD,
            EntityType.ID_NUMBER,
            EntityType.PASSPORT,
            EntityType.IBAN,
            EntityType.CARD_NUMBER,
            EntityType.IP_ADDRESS,
            EntityType.COORDINATES,
        }

    @pytest.mark.parametrize(
        ("kind", "text", "value"), POSITIVES, ids=[f"{k}:{v}" for k, _, v in POSITIVES]
    )
    def test_it_finds_each_known_format(self, kind, text, value):
        assert (kind, value) in found(text)

    @pytest.mark.parametrize("text", NEGATIVES)
    def test_it_leaves_ordinary_text_alone(self, text):
        assert found(text) == []

    def test_every_span_names_the_pattern_detector(self):
        spans = asyncio.run(DETECTOR.detect("MRN 448-1207, amina@example.org"))

        assert {s.source for s in spans} == {"patterns@1"}

    def test_an_mrn_is_masked_but_its_label_is_kept_for_the_reader(self):
        assert found("MRN 448-1207") == [(EntityType.MEDICAL_RECORD, "448-1207")]

    def test_each_value_is_reported_once_even_when_patterns_overlap(self):
        spans = asyncio.run(DETECTOR.detect("Tel: +254 712 345 678"))

        assert len(spans) == 1

    def test_a_spaced_invalid_iban_may_still_be_masked_as_a_phone_number(self):
        """Known and accepted: "0076 2011 …" is also the shape of an international number.

        Over-masking a mistyped account number costs little; reading it as
        harmless would be the leak.
        """
        assert types_in("IBAN CH93 0076 2011 6238 5295 8") == {EntityType.PHONE}

    def test_an_empty_text_has_nothing_to_find(self):
        assert found("") == []
