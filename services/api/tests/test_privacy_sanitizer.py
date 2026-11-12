"""The Sanitizer seam, the local NER adapter, and the last check before any model call.

A span found by any detector is masked, project rules apply, and the last check on what
leaves fails closed. Every detector runs inside the deployment, never `openai_filter`.
"""

from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass
from typing import ClassVar

import pytest

from app.api.dependencies import ner_detectors, planning_sanitizer, prompt_guard
from app.application.inference import GuardedInference
from app.domain.inference import ChatMessage, ChatRequest, ChatRole, PromptRefusedError
from app.domain.masking import (
    DOCUMENT_ENTITY_TYPES,
    PAPER_ENTITY_TYPES,
    QUESTION_ENTITY_TYPES,
    EntityType,
    MaskingRule,
    rule_source,
)
from app.inference.fake import FakeInferenceProvider
from app.privacy.base import Detector, Sanitizer
from app.privacy.fake import FakeDetector
from app.privacy.guard import LeakCheckPromptGuard
from app.privacy.patterns import PatternDetector
from app.privacy.sanitizer import DetectorSanitizer
from app.privacy.spacy_ner import SPACY_LABELS, RemoteModelRefusedError, SpacyNerDetector
from app.settings import Environment, Settings

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}
ANSWER = {"answer": "ok"}


def request(*texts: str) -> ChatRequest:
    return ChatRequest(
        messages=tuple(ChatMessage(ChatRole.USER, text) for text in texts),
        output_schema=SCHEMA,
    )


def rule(term: str, kind: EntityType = EntityType.PERSON) -> MaskingRule:
    return MaskingRule(uuid.uuid4(), term, kind)


NAMES = FakeDetector(
    {
        "Amina Yusuf": EntityType.PERSON,
        "Marib": EntityType.LOCATION,
        "12 March": EntityType.DATE,
    }
)


def sanitizer(entity_types=QUESTION_ENTITY_TYPES) -> DetectorSanitizer:
    return DetectorSanitizer([PatternDetector(), NAMES], entity_types=entity_types)


# The local name model labels "Sahel" and "Niger" as people (checked 2026-11-08
# with en_core_web_sm); the fake reproduces that.
PLACES = FakeDetector(
    {
        "Sahel": EntityType.PERSON,
        "Mali": EntityType.LOCATION,
        "Kakuma": EntityType.LOCATION,
        "Amina Yusuf": EntityType.PERSON,
    }
)
QUESTION = "Did cash help Amina Yusuf in Kakuma, Mali, and across the Sahel?"


class TestCountriesAndRegions:
    """An operator setting, off by default (ADR 0011)."""

    async def test_by_default_countries_and_regions_are_masked_like_any_place(self):
        masker = DetectorSanitizer([PLACES], entity_types=QUESTION_ENTITY_TYPES)

        result = await masker.mask(QUESTION, rules=[])

        assert "Mali" not in result.masked
        assert "Sahel" not in result.masked

    async def test_when_kept_countries_and_regions_stay_and_everything_smaller_is_masked(self):
        masker = DetectorSanitizer(
            [PLACES], entity_types=QUESTION_ENTITY_TYPES, keeps_countries_and_regions=True
        )

        result = await masker.mask(QUESTION, rules=[])

        assert result.masked == (
            "Did cash help [PERSON_1] in [LOCATION_1], Mali, and across the Sahel?"
        )

    async def test_a_project_rule_still_masks_a_kept_name(self):
        masker = DetectorSanitizer(
            [PLACES], entity_types=QUESTION_ENTITY_TYPES, keeps_countries_and_regions=True
        )

        result = await masker.mask(QUESTION, rules=[rule("Mali", EntityType.LOCATION)])

        assert "Mali" not in result.masked
        assert "Sahel" in result.masked

    @pytest.mark.parametrize("is_kept", [False, True])
    def test_the_planning_sanitizer_follows_the_operators_setting(self, is_kept):
        settings = Settings(
            environment=Environment.TEST, mask_countries_and_regions=not is_kept
        )

        masker = planning_sanitizer(settings, (PLACES,))

        assert isinstance(masker, DetectorSanitizer)
        assert masker.keeps_countries_and_regions is is_kept


class TestFakeDetector:
    async def test_it_finds_every_occurrence_of_its_values_for_real(self):
        spans = await NAMES.detect("Amina Yusuf and amina yusuf in Marib")

        assert [(s.start, s.end, s.entity_type) for s in spans] == [
            (0, 11, EntityType.PERSON),
            (16, 27, EntityType.PERSON),
            (31, 36, EntityType.LOCATION),
        ]

    def test_it_conforms_to_the_detector_protocol(self):
        detector: Detector = NAMES

        assert detector.source == "fake@1"


class TestDetectorSanitizer:
    async def test_it_conforms_to_the_sanitizer_contract_other_lanes_use(self):
        masker: Sanitizer = sanitizer()

        result = await masker.mask("Ask Amina Yusuf.", rules=[])

        assert result.masked == "Ask [PERSON_1]."

    async def test_a_span_found_by_any_detector_is_masked(self):
        text = "Amina Yusuf (amina@example.org, MRN 448-1207) lives in Marib."

        result = await sanitizer().mask(text, rules=[])

        assert result.masked == (
            "[PERSON_1] ([EMAIL_1], MRN [MEDICAL_RECORD_1]) lives in [LOCATION_1]."
        )
        assert dict(result.counts) == {
            "EMAIL": 1,
            "LOCATION": 1,
            "MEDICAL_RECORD": 1,
            "PERSON": 1,
        }

    async def test_project_rules_are_masked_as_their_chosen_type_and_named_as_source(self):
        clinic = rule("Al Noor Clinic", EntityType.LOCATION)

        result = await sanitizer().mask("Seen at al noor clinic.", rules=[clinic])

        assert result.masked == "Seen at [LOCATION_1]."
        assert [s.source for s in result.spans] == [rule_source(clinic.id)]

    async def test_project_rules_apply_whatever_the_entity_list(self):
        """A rule is a person's explicit decision; no list can switch it off."""
        papers = sanitizer(PAPER_ENTITY_TYPES)

        result = await papers.mask("Funded by Amina Yusuf.", rules=[rule("Amina Yusuf")])

        assert result.masked == "Funded by [PERSON_1]."

    async def test_the_paper_list_leaves_names_places_and_dates_alone(self):
        text = "Amina Yusuf studied Marib from 12 March; write to amina@example.org."

        result = await sanitizer(PAPER_ENTITY_TYPES).mask(text, rules=[])

        assert result.masked == ("Amina Yusuf studied Marib from 12 March; write to [EMAIL_1].")

    async def test_the_document_list_also_masks_dates(self):
        result = await sanitizer(DOCUMENT_ENTITY_TYPES).mask("Seen on 12 March.", rules=[])

        assert result.masked == "Seen on [DATE_1]."

    def test_its_sources_name_only_the_detectors_its_list_needs(self):
        """The manifest must say which detectors actually looked at the text."""
        assert sanitizer(PAPER_ENTITY_TYPES).sources == ("patterns@1",)
        assert sanitizer(QUESTION_ENTITY_TYPES).sources == ("fake@1", "patterns@1")

    async def test_a_detector_outside_the_list_is_never_run(self):
        calls: list[str] = []

        class Counting(FakeDetector):
            async def detect(self, text: str):
                calls.append(text)
                return await super().detect(text)

        names = Counting({"Amina": EntityType.PERSON})
        masker = DetectorSanitizer([PatternDetector(), names], entity_types=PAPER_ENTITY_TYPES)

        await masker.mask("Amina", rules=[])

        assert calls == []

    def test_a_sanitizer_needs_at_least_one_detector(self):
        with pytest.raises(ValueError, match="detectors"):
            DetectorSanitizer([], entity_types=QUESTION_ENTITY_TYPES)


@dataclass(frozen=True)
class _RecordedEntity:
    start_char: int
    end_char: int
    label_: str


@dataclass(frozen=True)
class _RecordedDoc:
    ents: tuple[_RecordedEntity, ...]


class RecordedLanguage:
    """What en_core_web_sm 3.8.0 returned for these sentences, recorded on 2026-11-03."""

    @property
    def meta(self) -> dict[str, str]:
        return {"lang": "en", "name": "core_web_sm", "version": "3.8.0"}

    _OUTPUTS: ClassVar[dict[str, tuple[_RecordedEntity, ...]]] = {
        "Dr Amina Traoré met Kofi Mensah at the clinic in Marib on 12 March 2023.": (
            _RecordedEntity(3, 15, "PERSON"),
            _RecordedEntity(20, 31, "PERSON"),
            _RecordedEntity(49, 54, "GPE"),
            _RecordedEntity(58, 71, "DATE"),
        ),
        "Cash transfers reduced acute malnutrition among children in Kenya and "
        "Somalia between 2019 and 2021.": (
            _RecordedEntity(60, 65, "GPE"),
            _RecordedEntity(70, 77, "GPE"),
            _RecordedEntity(78, 99, "DATE"),
        ),
        "UNICEF paid 40 dollars to 300 families.": (
            _RecordedEntity(0, 6, "ORG"),
            _RecordedEntity(12, 22, "MONEY"),
            _RecordedEntity(26, 29, "CARDINAL"),
        ),
    }

    def __call__(self, text: str) -> _RecordedDoc:
        return _RecordedDoc(self._OUTPUTS.get(text, ()))


class TestSpacyNerAdapter:
    async def test_names_places_and_dates_become_spans_with_the_model_as_source(self):
        detector = SpacyNerDetector(RecordedLanguage())
        text = "Dr Amina Traoré met Kofi Mensah at the clinic in Marib on 12 March 2023."

        spans = await detector.detect(text)

        assert [(text[s.start : s.end], s.entity_type) for s in spans] == [
            ("Amina Traoré", EntityType.PERSON),
            ("Kofi Mensah", EntityType.PERSON),
            ("Marib", EntityType.LOCATION),
            ("12 March 2023", EntityType.DATE),
        ]
        assert {s.source for s in spans} == {"spacy-en_core_web_sm@3.8.0"}

    async def test_labels_outside_the_mapping_are_ignored(self):
        """Organisations, sums and counts are evidence, not personal data."""
        detector = SpacyNerDetector(RecordedLanguage())

        assert await detector.detect("UNICEF paid 40 dollars to 300 families.") == ()

    def test_it_maps_spacy_labels_onto_three_entity_types(self):
        assert dict(SPACY_LABELS) == {
            "PERSON": EntityType.PERSON,
            "GPE": EntityType.LOCATION,
            "LOC": EntityType.LOCATION,
            "FAC": EntityType.LOCATION,
            "DATE": EntityType.DATE,
        }
        assert SpacyNerDetector(RecordedLanguage()).entity_types == {
            EntityType.PERSON,
            EntityType.LOCATION,
            EntityType.DATE,
        }

    async def test_it_masks_through_the_sanitizer_like_any_detector(self):
        masker = DetectorSanitizer(
            [PatternDetector(), SpacyNerDetector(RecordedLanguage())],
            entity_types=QUESTION_ENTITY_TYPES,
        )
        text = (
            "Cash transfers reduced acute malnutrition among children in Kenya and "
            "Somalia between 2019 and 2021."
        )

        result = await masker.mask(text, rules=[])

        assert result.masked == (
            "Cash transfers reduced acute malnutrition among children in [LOCATION_1] and "
            "[LOCATION_2] between 2019 and 2021."
        )

    @pytest.mark.parametrize(
        "model",
        [
            "https://example.org/en_core_web_sm.whl",
            "hf://urchade/gliner_multi_pii-v1",
            "s3://bucket/model",
            "en_core_web_sm; import os",
            "",
        ],
    )
    def test_a_model_that_is_not_an_installed_package_or_local_folder_is_refused(self, model):
        """Loading must never fetch anything: a remote model is refused, not downloaded."""
        with pytest.raises(RemoteModelRefusedError):
            SpacyNerDetector.load(model)

    def test_availability_is_checked_without_loading_or_downloading(self):
        assert SpacyNerDetector.is_available("no_such_model_package_xyz") is False


_HAS_MODEL = importlib.util.find_spec("en_core_web_sm") is not None


@pytest.mark.skipif(not _HAS_MODEL, reason="the ner extra is not installed")
class TestSpacyNerWithTheRealModel:
    async def test_the_installed_model_finds_a_name_and_a_place(self):
        detector = SpacyNerDetector.load("en_core_web_sm")
        text = "Should we move Amina Traoré's team from Marib to Aden?"

        spans = await detector.detect(text)

        assert EntityType.PERSON in {s.entity_type for s in spans}
        assert {"Marib", "Aden"} <= {text[s.start : s.end] for s in spans}
        assert detector.source == "spacy-en_core_web_sm@3.8.0"


class TestLeakCheckPromptGuard:
    async def test_an_unmasked_email_in_a_prompt_is_refused(self):
        guard = LeakCheckPromptGuard([PatternDetector()])

        verdict = await guard.inspect(request("Screen this: write to amina@example.org."))

        assert not verdict.is_allowed

    async def test_the_refusal_names_the_entity_type_never_the_value(self):
        guard = LeakCheckPromptGuard([PatternDetector()])

        verdict = await guard.inspect(
            request("amina@example.org", "MRN 448-1207 and kofi@example.org")
        )

        assert "2 EMAIL" in verdict.reason
        assert "1 MEDICAL_RECORD" in verdict.reason
        assert "amina" not in verdict.reason
        assert "448" not in verdict.reason

    async def test_placeholders_pass(self):
        guard = LeakCheckPromptGuard([PatternDetector()])

        verdict = await guard.inspect(
            request("[PERSON_1] wrote from [EMAIL_1] about MRN [MEDICAL_RECORD_1].")
        )

        assert verdict.is_allowed

    async def test_ordinary_screening_text_passes(self):
        guard = LeakCheckPromptGuard([PatternDetector()])

        verdict = await guard.inspect(
            request(
                "Criteria: children under 5 in Yemen, 2019-2023.",
                "Abstract: mortality fell (95% CI 1.2-3.4, p < 0.001); doi:10.1371/x.0123456",
            )
        )

        assert verdict.is_allowed

    async def test_text_masked_by_the_sanitizer_always_passes_the_guard(self):
        """The guard and the sanitizer share a detector, so masking is what the guard checks."""
        guard = LeakCheckPromptGuard([PatternDetector()])
        text = (
            "Call +254 712 345 678 or +967-1-234-5678, email amina [at] example [dot] org, "
            "MRN 448-1207, passport no. N1234567, IBAN CH93 0076 2011 6238 5295 7, "
            "card 4111 1111 1111 1111, host 192.168.10.24, camp at 15.3694, 44.1910."
        )
        masked = await sanitizer(PAPER_ENTITY_TYPES).mask(text, rules=[])

        assert (await guard.inspect(request(text))).is_allowed is False
        assert (await guard.inspect(request(masked.masked))).is_allowed is True


class TestGuardedInferenceWithTheRealGuard:
    async def test_an_unmasked_email_stops_the_call_before_it_is_sent(self):
        provider = FakeInferenceProvider([ANSWER])
        inference = GuardedInference(provider, LeakCheckPromptGuard([PatternDetector()]))

        with pytest.raises(PromptRefusedError, match="EMAIL"):
            await inference.chat(request("Contact amina@example.org about this."))

        assert provider.requests == []

    async def test_a_masked_email_goes_through(self):
        provider = FakeInferenceProvider([ANSWER])
        inference = GuardedInference(provider, LeakCheckPromptGuard([PatternDetector()]))

        result = await inference.chat(request("Contact [EMAIL_1] about this."))

        assert result.output == ANSWER
        assert len(provider.requests) == 1

    async def test_a_project_rule_term_stops_the_call_when_rules_are_given(self):
        provider = FakeInferenceProvider([ANSWER])
        inference = GuardedInference(provider, LeakCheckPromptGuard([PatternDetector()]))
        rules = [rule("Al Noor Clinic", EntityType.LOCATION)]

        with pytest.raises(PromptRefusedError, match="1 LOCATION"):
            await inference.with_rules(rules).chat(request("Patients at al noor clinic."))

        assert provider.requests == []

    async def test_the_rule_check_runs_after_the_guard_and_keeps_its_refusal(self):
        provider = FakeInferenceProvider([ANSWER])
        inference = GuardedInference(provider, LeakCheckPromptGuard([PatternDetector()]))

        with pytest.raises(PromptRefusedError, match="EMAIL"):
            await inference.with_rules([rule("Kofi")]).chat(request("a@example.org"))

    async def test_with_rules_keeps_the_model_and_lets_masked_text_through(self):
        provider = FakeInferenceProvider([ANSWER], model_id="local-model")
        inference = GuardedInference(provider, LeakCheckPromptGuard([PatternDetector()]))
        scoped = inference.with_rules([rule("Al Noor Clinic", EntityType.LOCATION)])

        result = await scoped.chat(request("Patients at [LOCATION_1]."))

        assert scoped.model_id == "local-model"
        assert result.output == ANSWER

    async def test_the_wired_guard_refuses_an_unmasked_email(self):
        """The guard `api/dependencies.py` puts in front of every model, checked offline."""
        provider = FakeInferenceProvider([ANSWER])
        inference = GuardedInference(provider, prompt_guard())

        with pytest.raises(PromptRefusedError, match="EMAIL"):
            await inference.chat(request("Contact amina@example.org about this."))
        assert provider.requests == []


class TestNerWiring:
    def test_off_means_no_name_model(self):
        assert ner_detectors(Settings(environment=Environment.TEST, masking_ner="off")) == ()

    def test_a_remote_model_is_refused_at_startup_rather_than_fetched(self):
        settings = Settings(
            environment=Environment.TEST,
            masking_ner="auto",
            masking_ner_model="https://example.org/model.whl",
        )

        with pytest.raises(RemoteModelRefusedError):
            ner_detectors(settings)

    def test_requiring_spacy_without_the_model_fails_loudly(self):
        settings = Settings(
            environment=Environment.TEST,
            masking_ner="spacy",
            masking_ner_model="no_such_model_package_xyz",
        )

        with pytest.raises(ValueError, match="not installed"):
            ner_detectors(settings)

    def test_auto_without_the_model_masks_patterns_and_rules_only(self):
        settings = Settings(
            environment=Environment.TEST,
            masking_ner="auto",
            masking_ner_model="no_such_model_package_xyz",
        )

        assert ner_detectors(settings) == ()
