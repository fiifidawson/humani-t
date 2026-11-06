"""The rules of a team's own documents, as pure functions.

Sources: the brief's rules 2, 3 and 12 (raw text kept apart; documents always
masked with the broad list; allowed types only, size limits), architecture
§3.9.1 (masking is derived and correctable; "not personal" never for
structured identifiers; review before use), UI_UX_SPEC §6.6, §6.13 and §8.4
(the `D` tag a citation shows).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.documents import (
    Document,
    DocumentDecision,
    DocumentFailure,
    DocumentRefusedError,
    DocumentStateError,
    DocumentStatus,
    DocumentType,
    DocumentUsage,
    ExtractedDocument,
    ExtractionLimits,
    MaskDetail,
    MaskExemption,
    ScanOutcome,
    ScanVerdict,
    UploadPolicy,
    UploadRefusal,
    detect_document_type,
    document_passages,
    masked_position,
    masked_regions,
    normalize_reason,
    remask,
    removable_spans,
    safe_filename,
    user_span,
)
from app.domain.masking import EntityType, MaskSpan, apply_spans, rule_source, user_source
from app.domain.screening import PassageSourceKind
from app.privacy.dates import DatePatternDetector
from tests.document_support import docx, pdf

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")
RULE = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _document(**changes: object) -> Document:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "number": 3,
        "name_sealed": b"sealed",
        "file_type": DocumentType.TXT,
        "size_bytes": 120,
        "status": DocumentStatus.UPLOADED,
        "uploaded_by": USER,
        "uploaded_at": NOW,
    }
    values.update(changes)
    return Document(**values)  # type: ignore[arg-type]


def _extracted(text: str = "Some text") -> ExtractedDocument:
    return ExtractedDocument(text, (0,), is_truncated=False, extractor="local@1")


class TestTypeDetection:
    def test_a_pdf_is_recognised_by_its_bytes_whatever_its_name(self):
        assert detect_document_type(pdf("hello"), "notes.txt") is DocumentType.PDF

    def test_a_docx_is_recognised_by_its_parts(self):
        assert detect_document_type(docx("hello"), "report.pdf") is DocumentType.DOCX

    def test_a_zip_that_is_not_a_word_document_is_refused(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("payload.exe", b"MZ")

        with pytest.raises(DocumentRefusedError) as refused:
            detect_document_type(buffer.getvalue(), "report.docx")
        assert refused.value.reason is UploadRefusal.UNSUPPORTED_TYPE

    def test_a_macro_enabled_document_renamed_docx_is_refused(self):
        content = docx("hello", extra={"word/vbaProject.bin": b"\x00macro"})

        with pytest.raises(DocumentRefusedError):
            detect_document_type(content, "report.docx")

    def test_utf8_text_is_text_and_markdown_only_by_its_extension(self):
        assert detect_document_type(b"# Minutes\n", "minutes.md") is DocumentType.MD
        assert detect_document_type(b"# Minutes\n", "minutes.txt") is DocumentType.TXT
        assert detect_document_type(b"# Minutes\n", "minutes.exe") is DocumentType.TXT

    def test_a_binary_file_named_txt_is_refused(self):
        with pytest.raises(DocumentRefusedError) as refused:
            detect_document_type(b"MZ\x90\x00\x03\x00\x00\x00", "notes.txt")
        assert refused.value.reason is UploadRefusal.UNSUPPORTED_TYPE

    def test_text_that_is_not_utf8_is_refused(self):
        with pytest.raises(DocumentRefusedError):
            detect_document_type("café".encode("cp1252"), "notes.txt")

    def test_an_empty_file_is_refused(self):
        with pytest.raises(DocumentRefusedError) as refused:
            detect_document_type(b"", "empty.txt")
        assert refused.value.reason is UploadRefusal.EMPTY


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("given", "shown"),
        [
            ("../../etc/passwd", "passwd"),
            ("C:\\Users\\amina\\report.docx", "report.docx"),
            ("report‮fdp.exe", "reportfdp.exe"),
            ("minutes\r\n2026.txt", "minutes2026.txt"),
            ('what?<is>"this".pdf', "what__is__this_.pdf"),
            ("   ", "document"),
            ("...", "document"),
            ("zero​width.txt", "zerowidth.txt"),
        ],
    )
    def test_names_lose_paths_and_hidden_or_unsafe_characters(self, given, shown):
        assert safe_filename(given) == shown

    def test_a_long_name_is_cut_but_keeps_its_extension(self):
        shown = safe_filename("a" * 400 + ".docx")

        assert len(shown) == 200
        assert shown.endswith(".docx")


class TestUploadPolicy:
    POLICY = UploadPolicy(
        max_file_bytes=1000, max_documents=2, max_project_bytes=1500, uploads_per_hour=5
    )

    def test_a_file_over_the_size_limit_is_refused(self):
        with pytest.raises(DocumentRefusedError) as refused:
            self.POLICY.check_size(1001)
        assert refused.value.reason is UploadRefusal.TOO_LARGE

    def test_the_document_count_quota_holds(self):
        with pytest.raises(DocumentRefusedError) as refused:
            self.POLICY.check_quota(DocumentUsage(2, 10), 10)
        assert refused.value.reason is UploadRefusal.QUOTA_EXCEEDED

    def test_the_byte_quota_holds(self):
        with pytest.raises(DocumentRefusedError):
            self.POLICY.check_quota(DocumentUsage(1, 1000), 501)

    def test_within_both_quotas_passes(self):
        self.POLICY.check_quota(DocumentUsage(1, 1000), 500)

    def test_a_disabled_policy_says_why(self):
        policy = UploadPolicy(1, 1, 1, 1, disabled_reason="No scanner.")

        assert not policy.is_enabled


class TestDocumentLifecycle:
    def test_a_document_is_tagged_by_its_number(self):
        assert _document(number=7).tag == "D7"

    def test_processing_walks_from_upload_to_review(self):
        until = NOW + timedelta(minutes=5)
        document = _document().claimed("worker-a", until)
        assert document.status is DocumentStatus.SCANNING
        assert document.attempts == 1
        document = document.scanned("clamd", until).extracted(_extracted(), until)
        assert document.status is DocumentStatus.MASKING

        ready = document.masked({"PERSON": 2})

        assert ready.status is DocumentStatus.AWAITING_REVIEW
        assert ready.mask_version == 1
        assert ready.claimed_by is None
        assert dict(ready.masked_counts) == {"PERSON": 2}

    def test_a_document_cannot_be_approved_while_it_is_still_being_read(self):
        with pytest.raises(DocumentStateError):
            _document().decided(DocumentDecision.APPROVE, 1)

    def test_an_approval_must_name_the_current_masked_version(self):
        waiting = _document(status=DocumentStatus.AWAITING_REVIEW, mask_version=2)

        with pytest.raises(DocumentStateError):
            waiting.decided(DocumentDecision.APPROVE, 1)
        assert waiting.decided(DocumentDecision.APPROVE, 2).status is DocumentStatus.APPROVED

    def test_a_correction_after_approval_sends_the_document_back_to_review(self):
        approved = _document(status=DocumentStatus.APPROVED, mask_version=1)

        corrected = approved.corrected({"PERSON": 1})

        assert corrected.status is DocumentStatus.AWAITING_REVIEW
        assert corrected.mask_version == 2

    def test_a_rejected_document_cannot_be_corrected(self):
        with pytest.raises(DocumentStateError):
            _document(status=DocumentStatus.REJECTED, mask_version=1).corrected({})

    def test_a_failure_is_recorded_exactly_when_failed(self):
        with pytest.raises(ValueError, match="failure"):
            _document(failure=DocumentFailure.UNREADABLE)
        failed = _document().failed(DocumentFailure.UNREADABLE)
        assert failed.status is DocumentStatus.FAILED

    def test_a_claim_is_held_exactly_while_processing(self):
        with pytest.raises(ValueError, match="claimed"):
            _document(status=DocumentStatus.SCANNING)


class TestCorrections:
    RAW = "Met Dr Amina Yusuf at Marib clinic, 12 March 2024, email a@b.org."

    def test_a_person_can_mask_text_the_detectors_missed(self):
        span = user_span(self.RAW, 7, 18, EntityType.PERSON, USER)

        assert span.source == user_source(USER)
        assert apply_spans(self.RAW, [span]).masked.startswith("Met Dr [PERSON_1]")

    @pytest.mark.parametrize(("start", "end"), [(-1, 3), (5, 5), (10, 1000), (3, 4)])
    def test_a_range_outside_the_text_or_without_text_is_refused(self, start, end):
        with pytest.raises(ValueError, match="mask"):
            user_span(self.RAW, start, end, EntityType.PERSON, USER)

    def test_a_detected_name_can_be_marked_not_personal(self):
        name = MaskSpan(25, 30, EntityType.LOCATION, "spacy-en_core_web_sm@3.8.0")

        assert removable_spans([name], 25, 30) == (name,)

    def test_a_detected_structured_identifier_can_never_be_unmasked(self):
        email = MaskSpan(59, 66, EntityType.EMAIL, "patterns@1")

        with pytest.raises(ValueError, match="Structured identifiers"):
            removable_spans([email], 59, 66)

    def test_a_project_rule_is_removed_by_deleting_the_rule_not_here(self):
        ruled = MaskSpan(7, 18, EntityType.PERSON, rule_source(RULE))

        with pytest.raises(ValueError, match="rule"):
            removable_spans([ruled], 7, 18)

    def test_a_person_may_remove_their_own_mask_of_any_type(self):
        mine = MaskSpan(7, 18, EntityType.ID_NUMBER, user_source(USER))

        assert removable_spans([mine], 7, 18) == (mine,)

    def test_removing_needs_the_exact_range_of_a_mask(self):
        name = MaskSpan(25, 30, EntityType.LOCATION, "spacy-en_core_web_sm@3.8.0")

        with pytest.raises(LookupError):
            removable_spans([name], 25, 29)

    @pytest.mark.parametrize("reason", ["", "   ", "x" * 201, "district‮"])
    def test_a_reason_must_be_one_short_visible_line(self, reason):
        with pytest.raises(ValueError, match="reason"):
            normalize_reason(reason)

    def test_a_mask_detail_survives_sealing_as_json(self):
        detail = MaskDetail(
            (MaskSpan(0, 3, EntityType.PERSON, "patterns@1"),),
            (MaskExemption(5, 9, EntityType.LOCATION, "spacy@3", USER, "A district"),),
            (0, 40),
        )

        assert MaskDetail.from_json(detail.to_json()) == detail


class TestMaskedPositions:
    def test_regions_say_where_each_placeholder_landed_and_what_made_it(self):
        raw = "Call Amina on 12 March or Amina again"
        spans = [
            MaskSpan(5, 10, EntityType.PERSON, "spacy@3"),
            MaskSpan(5, 10, EntityType.PERSON, rule_source(RULE)),
            MaskSpan(14, 22, EntityType.DATE, "dates@1"),
            MaskSpan(26, 31, EntityType.PERSON, "spacy@3"),
        ]
        masked = apply_spans(raw, spans)

        regions = masked_regions(masked)

        assert [masked.masked[r.masked_start : r.masked_end] for r in regions] == [
            "[PERSON_1]",
            "[DATE_1]",
            "[PERSON_1]",
        ]
        assert regions[0].sources == ("rule:" + str(RULE), "spacy@3")

    def test_a_raw_position_maps_past_every_earlier_placeholder(self):
        raw = "Amina met Kofi.\n\nPage two starts here."
        spans = [
            MaskSpan(0, 5, EntityType.PERSON, "spacy@3"),
            MaskSpan(10, 14, EntityType.PERSON, "spacy@3"),
        ]
        masked, starts = remask(raw, spans, (0, 17))

        assert masked.masked[starts[1] :] == "Page two starts here."

    def test_a_position_inside_a_mask_lands_on_its_placeholder(self):
        masked = apply_spans("Amina met", [MaskSpan(0, 5, EntityType.PERSON, "spacy@3")])

        assert masked_position(masked_regions(masked), 2) == 0


class TestPassages:
    def test_passages_carry_the_documents_tag_kind_and_id(self):
        document = _document(number=2, file_type=DocumentType.TXT)

        (passage,) = document_passages(document, "A short masked note about [PERSON_1].", (0,))

        assert passage.tag == "D2"
        assert passage.source_kind is PassageSourceKind.DOCUMENT
        assert passage.paper_id == document.id
        assert passage.locator == "characters 0-37"

    def test_a_pdf_passage_names_its_page(self):
        document = _document(file_type=DocumentType.PDF)
        text = "First page text.\n\nSecond page text."

        passages = document_passages(document, text, (0, 18), size=20)

        assert [p.locator for p in passages] == ["page 1", "page 2"]

    def test_long_text_is_cut_at_whitespace_and_never_inside_a_placeholder(self):
        document = _document()
        text = " ".join(["[PERSON_12] met the team"] * 200)

        passages = document_passages(document, text, (0,), size=100)

        assert len(passages) > 1
        assert all(p.text.count("[") == p.text.count("]") for p in passages)
        assert "".join(p.text for p in passages).count("[PERSON_12]") == 200


class TestDates:
    @pytest.mark.parametrize(
        "date",
        [
            "12/03/2024",
            "2024-03-12",
            "12 March 2024",
            "March 12, 2024",
            "3rd of June",
            "March 2024",
        ],
    )
    def test_dates_that_name_a_day_or_month_are_found(self, date):
        text = f"Seen on {date}."

        spans = asyncio.run(DatePatternDetector().detect(text))

        assert [text[s.start : s.end] for s in spans] == [date]

    @pytest.mark.parametrize(
        "text", ["In 2019 the camp grew.", "Version 1.2.3", "12 may attend"]
    )
    def test_bare_years_and_other_numbers_are_left_alone(self, text):
        assert asyncio.run(DatePatternDetector().detect(text)) == ()


class TestSmallTypes:
    def test_a_scan_verdict_names_a_signature_only_when_infected(self):
        with pytest.raises(ValueError, match="signature"):
            ScanVerdict(ScanOutcome.CLEAN, "clamd", "Eicar")
        assert ScanVerdict(ScanOutcome.INFECTED, "clamd", "Eicar").signature == "Eicar"

    def test_extraction_limits_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            ExtractionLimits(max_pages=0)

    def test_unpaged_text_has_no_page_count(self):
        assert _extracted().page_count is None
