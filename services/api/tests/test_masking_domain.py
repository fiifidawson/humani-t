"""Masking as a pure function of the raw text and its spans.

The property tests carry the invariants: nothing inside a masked span survives, the result
does not depend on span order, and spans keep pointing into the unchanged raw text. A
placeholder's number keeps one person apart from another in the same text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.masking import (
    DOCUMENT_ENTITY_TYPES,
    PAPER_ENTITY_TYPES,
    PLACEHOLDER,
    QUESTION_ENTITY_TYPES,
    STRUCTURED_ENTITY_TYPES,
    EntityType,
    MaskedText,
    MaskingRule,
    MaskingRuleRecord,
    MaskSpan,
    apply_spans,
    detector_source,
    holds_placeholder,
    leak_reason,
    masking_fingerprint,
    normalize_masking_term,
    rule_source,
    rule_spans,
    spans_from_json,
    spans_to_json,
    unmasked,
    user_source,
)

PATTERNS = detector_source("patterns", "1")
NER = detector_source("spacy-en_core_web_sm", "3.8.0")
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def span(start: int, end: int, kind: EntityType = EntityType.PERSON, source: str = NER):
    return MaskSpan(start, end, kind, source)


def spans_of(text: str, *values: tuple[str, EntityType]) -> list[MaskSpan]:
    """A span over every occurrence of each value: how a detector reports them."""
    found = []
    for value, kind in values:
        start = text.find(value)
        while start != -1:
            found.append(span(start, start + len(value), kind))
            start = text.find(value, start + 1)
    return found


def rule(term: str, kind: EntityType = EntityType.PERSON) -> MaskingRule:
    return MaskingRule(uuid.uuid4(), term, kind)


class TestMaskSpan:
    def test_a_span_must_cover_at_least_one_character(self):
        with pytest.raises(ValueError, match=r"MaskSpan.end"):
            MaskSpan(4, 4, EntityType.EMAIL, PATTERNS)

    def test_a_span_cannot_start_before_the_text(self):
        with pytest.raises(ValueError, match=r"MaskSpan.start"):
            MaskSpan(-1, 3, EntityType.EMAIL, PATTERNS)

    @pytest.mark.parametrize(
        "source",
        [
            "patterns@1",
            "spacy-en_core_web_sm@3.8.0",
            f"user:{uuid.UUID(int=7)}",
            f"rule:{uuid.UUID(int=9)}",
        ],
    )
    def test_a_span_names_a_detector_and_version_a_user_or_a_project_rule(self, source):
        assert MaskSpan(0, 3, EntityType.PERSON, source).source == source

    @pytest.mark.parametrize("source", ["", "patterns", "user:alice", "@1", "rule:"])
    def test_a_span_without_a_traceable_source_is_refused(self, source):
        """Provenance: 'why was this masked, and by what?' must always have an answer."""
        with pytest.raises(ValueError, match=r"MaskSpan.source"):
            MaskSpan(0, 3, EntityType.PERSON, source)

    def test_source_helpers_build_the_three_shapes(self):
        user_id, rule_id = uuid.uuid4(), uuid.uuid4()

        assert detector_source("patterns", "1") == "patterns@1"
        assert user_source(user_id) == f"user:{user_id}"
        assert rule_source(rule_id) == f"rule:{rule_id}"

    def test_spans_survive_a_json_round_trip_for_sealed_storage(self):
        spans = (span(0, 4), span(10, 20, EntityType.EMAIL, PATTERNS))

        assert spans_from_json(spans_to_json(spans)) == spans


class TestEntityLists:
    def test_papers_get_the_narrow_list_of_structured_identifiers_only(self):
        """Papers name places and authors as evidence; masking them breaks screening."""
        assert PAPER_ENTITY_TYPES == STRUCTURED_ENTITY_TYPES
        assert EntityType.PERSON not in PAPER_ENTITY_TYPES
        assert EntityType.LOCATION not in PAPER_ENTITY_TYPES

    def test_a_question_also_masks_names_and_places(self):
        assert (
            STRUCTURED_ENTITY_TYPES
            | {
                EntityType.PERSON,
                EntityType.LOCATION,
            }
            == QUESTION_ENTITY_TYPES
        )

    def test_uploaded_documents_get_the_broad_list_with_dates(self):
        """Brief rule 3: names, emails, phones, dates, places, medical record and ID numbers."""
        assert QUESTION_ENTITY_TYPES | {EntityType.DATE} == DOCUMENT_ENTITY_TYPES

    def test_medical_record_numbers_are_on_every_list(self):
        """Brief rule 5 names MRNs as the format masking used to miss."""
        for entity_types in (PAPER_ENTITY_TYPES, QUESTION_ENTITY_TYPES, DOCUMENT_ENTITY_TYPES):
            assert EntityType.MEDICAL_RECORD in entity_types


class TestApplySpans:
    def test_text_with_no_spans_is_returned_unchanged_with_no_counts(self):
        result = apply_spans("Cash or food?", [])

        assert result == MaskedText("Cash or food?", (), {})

    def test_each_span_becomes_a_typed_numbered_placeholder(self):
        raw = "Call Amina Yusuf on +254 712 345 678."
        spans = spans_of(
            raw, ("Amina Yusuf", EntityType.PERSON), ("+254 712 345 678", EntityType.PHONE)
        )

        assert apply_spans(raw, spans).masked == "Call [PERSON_1] on [PHONE_1]."

    def test_the_same_value_gets_the_same_placeholder_within_one_text(self):
        raw = "Amina met Kofi. Later Amina called."
        spans = spans_of(raw, ("Amina", EntityType.PERSON), ("Kofi", EntityType.PERSON))

        assert (
            apply_spans(raw, spans).masked
            == "[PERSON_1] met [PERSON_2]. Later [PERSON_1] called."
        )

    def test_the_same_value_matches_regardless_of_case_and_spacing(self):
        raw = "Amina Yusuf, then AMINA  YUSUF."
        spans = [span(0, 11), span(18, 30)]

        assert apply_spans(raw, spans).masked == "[PERSON_1], then [PERSON_1]."

    def test_numbers_count_separately_per_entity_type(self):
        raw = "Amina in Marib; Kofi in Aden."
        spans = spans_of(
            raw,
            ("Amina", EntityType.PERSON),
            ("Marib", EntityType.LOCATION),
            ("Kofi", EntityType.PERSON),
            ("Aden", EntityType.LOCATION),
        )

        assert apply_spans(raw, spans).masked == (
            "[PERSON_1] in [LOCATION_1]; [PERSON_2] in [LOCATION_2]."
        )

    def test_counts_are_distinct_values_by_type_never_the_values(self):
        """The receipt says '2 people, 1 location'."""
        raw = "Amina met Kofi and Amina again in Marib."
        spans = spans_of(
            raw,
            ("Amina", EntityType.PERSON),
            ("Kofi", EntityType.PERSON),
            ("Marib", EntityType.LOCATION),
        )

        result = apply_spans(raw, spans)

        assert dict(result.counts) == {"LOCATION": 1, "PERSON": 2}
        assert result.total == 3

    def test_overlapping_spans_merge_and_the_wider_region_is_masked(self):
        """Over-masking costs some quality; under-masking leaks."""
        raw = "Contact Dr Amina Yusuf today."
        spans = [span(8, 16), span(11, 22)]

        assert apply_spans(raw, spans).masked == "Contact [PERSON_1] today."

    def test_a_structured_identifier_wins_the_type_of_a_merged_region(self):
        raw = "Write to amina.yusuf@example.org now."
        spans = [
            span(9, 20, EntityType.PERSON),
            span(9, 32, EntityType.EMAIL, PATTERNS),
        ]

        result = apply_spans(raw, spans)

        assert result.masked == "Write to [EMAIL_1] now."
        assert dict(result.counts) == {"EMAIL": 1}

    def test_adjacent_spans_stay_separate(self):
        raw = "AminaKofi"
        spans = [span(0, 5), span(5, 9)]

        assert apply_spans(raw, spans).masked == "[PERSON_1][PERSON_2]"

    def test_the_original_spans_are_kept_sorted_and_without_duplicates(self):
        raw = "Amina and Kofi"
        spans = [span(10, 14), span(0, 5), span(0, 5)]

        assert apply_spans(raw, spans).spans == (span(0, 5), span(10, 14))

    def test_a_span_past_the_end_of_the_text_is_refused(self):
        with pytest.raises(ValueError, match=r"MaskSpan.end"):
            apply_spans("short", [span(0, 6)])

    def test_placeholders_match_the_shared_placeholder_pattern(self):
        raw = "MRN 448-1207"
        result = apply_spans(raw, [span(4, 12, EntityType.MEDICAL_RECORD, PATTERNS)])

        assert PLACEHOLDER.fullmatch(result.masked[4:])


# Secrets come from an alphabet that neither the filler text nor any
# placeholder uses, so "the secret does not appear" is meaningful even for a
# one-character secret.
_FILLER = st.text(alphabet="abcdefghij ,.", max_size=12)
_SECRET = st.text(alphabet="αβγδεζηθ", min_size=1, max_size=8)
_KIND = st.sampled_from(list(EntityType))


@st.composite
def texts_with_secrets(draw: st.DrawFn) -> tuple[str, list[MaskSpan], list[str]]:
    """A text built from filler and secrets, with a span over every secret."""
    pieces = draw(st.lists(st.tuples(_FILLER, _SECRET, _KIND), max_size=8))
    tail = draw(_FILLER)
    raw, spans, secrets = "", [], []
    for filler, secret, kind in pieces:
        raw += filler
        spans.append(span(len(raw), len(raw) + len(secret), kind))
        secrets.append(secret)
        raw += secret
    raw += tail
    extra = draw(st.lists(st.tuples(st.integers(0, 200), st.integers(1, 20), _KIND)))
    for start, length, kind in extra:
        if start < len(raw):
            spans.append(span(start, min(len(raw), start + length), kind))
    return raw, spans, secrets


class TestApplySpansProperties:
    @given(texts_with_secrets())
    def test_no_character_of_any_masked_value_survives(self, case):
        raw, spans, secrets = case

        masked = apply_spans(raw, spans).masked

        assert not any(ch in masked for secret in secrets for ch in secret)

    @given(texts_with_secrets(), st.randoms(use_true_random=False))
    def test_the_result_does_not_depend_on_the_order_of_the_spans(self, case, rnd):
        raw, spans, _ = case
        shuffled = list(spans)
        rnd.shuffle(shuffled)

        assert apply_spans(raw, spans) == apply_spans(raw, shuffled)

    @given(texts_with_secrets())
    def test_applying_twice_gives_the_same_result(self, case):
        raw, spans, _ = case

        assert apply_spans(raw, spans) == apply_spans(raw, spans)

    @given(texts_with_secrets())
    def test_the_spans_keep_their_offsets_into_the_raw_text(self, case):
        raw, spans, _ = case

        result = apply_spans(raw, spans)

        assert result.spans == tuple(sorted(set(spans)))

    @given(texts_with_secrets())
    def test_text_outside_every_span_is_kept_in_order(self, case):
        raw, spans, _ = case

        masked = apply_spans(raw, spans).masked
        covered = {i for s in spans for i in range(s.start, s.end)}
        kept = "".join(ch for i, ch in enumerate(raw) if i not in covered)

        assert PLACEHOLDER.sub("", masked) == kept


class TestMaskingRules:
    def test_a_term_is_trimmed_and_inner_whitespace_collapsed(self):
        assert normalize_masking_term("  Amina   Yusuf ") == "Amina Yusuf"

    @pytest.mark.parametrize("term", ["", "   ", "A", "...", "x" * 201, "Ami\x00na", "Ami‮na"])
    def test_an_unusable_term_is_refused(self, term):
        with pytest.raises(ValueError, match=r"MaskingRule.term"):
            normalize_masking_term(term)

    def test_a_rule_must_hold_a_normalised_term(self):
        with pytest.raises(ValueError, match=r"MaskingRule.term"):
            MaskingRule(uuid.uuid4(), " Amina ", EntityType.PERSON)

    def test_a_stored_rule_holds_only_the_sealed_term(self):
        record = MaskingRuleRecord(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            term_sealed=b"sealed",
            entity_type=EntityType.PERSON,
            created_by=uuid.uuid4(),
            created_at=NOW,
        )

        assert not hasattr(record, "term")

    def test_a_stored_rule_needs_an_aware_timestamp(self):
        with pytest.raises(ValueError, match=r"created_at"):
            MaskingRuleRecord(
                id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                term_sealed=b"sealed",
                entity_type=EntityType.PERSON,
                created_by=uuid.uuid4(),
                created_at=datetime(2026, 11, 3),  # noqa: DTZ001 — the naive value under test
            )


class TestRuleSpans:
    def test_a_rule_matches_whole_words_regardless_of_case(self):
        rules = [rule("Traoré")]
        text = "Dr TRAORÉ met traoré; Traoréville is a town."

        found = rule_spans(text, rules)

        assert [text[s.start : s.end] for s in found] == ["TRAORÉ", "traoré"]

    def test_a_multi_word_rule_matches_across_any_whitespace(self):
        rules = [rule("Amina Yusuf")]
        text = "Amina\n  Yusuf signed."

        assert [(s.start, s.end) for s in rule_spans(text, rules)] == [(0, 13)]

    def test_a_rule_span_names_the_rule_as_its_source_and_carries_its_type(self):
        clinic = rule("Al Noor Clinic", EntityType.LOCATION)

        (found,) = rule_spans("Seen at Al Noor Clinic.", [clinic])

        assert found.source == rule_source(clinic.id)
        assert found.entity_type is EntityType.LOCATION

    def test_regex_characters_in_a_term_are_matched_literally(self):
        rules = [rule("O'Neil (MSF)")]

        assert len(rule_spans("Ask O'Neil (MSF) first.", rules)) == 1
        assert rule_spans("Ask ONeil MSF first.", rules) == ()

    def test_a_rule_never_matches_inside_a_placeholder(self):
        assert rule_spans("[PERSON_1] said so.", [rule("person")]) == ()


class TestPromptChecks:
    def test_spans_inside_placeholders_are_not_leaks(self):
        text = "Ask [PERSON_1] at [EMAIL_2] or amina@example.org"
        inside = span(5, 13, EntityType.PERSON)
        outside = span(31, 48, EntityType.EMAIL, PATTERNS)

        assert unmasked(text, [inside, outside]) == (outside,)

    def test_the_refusal_names_types_and_counts_never_values(self):
        reason = leak_reason({EntityType.EMAIL: 2, EntityType.PHONE: 1})

        assert "2 EMAIL" in reason
        assert "1 PHONE" in reason
        assert "@" not in reason

    def test_the_fingerprint_changes_when_a_detector_or_a_rule_changes(self):
        first = rule("Amina")
        base = masking_fingerprint([PATTERNS], [first])

        assert masking_fingerprint([PATTERNS], [first]) == base
        assert masking_fingerprint([detector_source("patterns", "2")], [first]) != base
        assert masking_fingerprint([PATTERNS], [first, rule("Kofi")]) != base
        assert masking_fingerprint([PATTERNS], []) != base


class TestHoldsPlaceholder:
    @pytest.mark.parametrize(
        "text",
        [
            "[LOCATION_1]",
            "displacement in [LOCATION_1]",
            "<LOCATION_1>",
            "{PERSON_2}",
            "LOCATION_1 region",
            "location_1",
            "Location-1 camps",
            "[MEDICAL_RECORD_3]",
            "medical record_3",
        ],
    )
    def test_a_placeholder_is_found_however_a_model_rewrote_its_brackets_or_case(self, text):
        assert holds_placeholder(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Sahel",
            "displacement",
            "COVID-19",
            "relocation",
            "relocation_1",
            "date 2020",
            "people 1 to 5",
        ],
    )
    def test_ordinary_search_terms_hold_no_placeholder(self, text):
        assert not holds_placeholder(text)

    @given(st.sampled_from(list(EntityType)), st.integers(min_value=1, max_value=999))
    def test_every_placeholder_masking_writes_is_found(self, kind, number):
        assert holds_placeholder(f"[{kind.value}_{number}]")
