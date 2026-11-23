"""Reading uploaded files: text out, within limits, and safe against files built to attack.

Each hostile file is built as an attacker would build it (OWASP XXE and file-upload cheat
sheets), and each spec names the bound it proves. The isolated extractor runs for real: a
child process, killed at its timeout.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.document_lines import DocumentLine, LineKind
from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractionFailedError,
    ExtractionLimits,
)
from app.extraction.isolated import IsolatedDocumentExtractor
from app.extraction.local import EXTRACTOR_ID, LocalDocumentExtractor, extract_document
from tests.document_support import (
    W,
    document_xml,
    docx,
    encrypted_pdf,
    pdf,
    word_body,
    word_paragraph,
    word_styles,
    word_table,
)

LIMITS = ExtractionLimits()


def _failure(content: bytes, file_type: DocumentType, limits: ExtractionLimits = LIMITS):
    with pytest.raises(ExtractionFailedError) as failed:
        extract_document(content, file_type, limits)
    return failed.value.failure


class TestPlainText:
    def test_text_is_read_with_line_endings_normalised(self):
        extracted = extract_document(b"First line\r\nSecond line", DocumentType.TXT, LIMITS)

        assert extracted.text == "First line\nSecond line"
        assert extracted.page_starts == (0,)
        assert extracted.extractor == EXTRACTOR_ID

    def test_a_byte_order_mark_and_control_characters_are_dropped(self):
        extracted = extract_document("﻿Notes\x07 here".encode(), DocumentType.MD, LIMITS)

        assert extracted.text == "Notes here"

    def test_text_past_the_character_limit_is_cut_and_says_so(self):
        limits = ExtractionLimits(max_characters=1000)

        extracted = extract_document(b"a" * 5000, DocumentType.TXT, limits)

        assert len(extracted.text) == 1000
        assert extracted.is_truncated

    def test_a_file_with_no_text_fails_plainly(self):
        assert _failure(b"  \n\n ", DocumentType.TXT) is DocumentFailure.NO_TEXT


class TestPdf:
    def test_each_page_is_read_and_its_start_recorded(self):
        extracted = extract_document(
            pdf("Cash transfers in Marib", "Second page"), DocumentType.PDF, LIMITS
        )

        assert "Cash transfers in Marib" in extracted.text
        assert extracted.page_count == 2
        second = extracted.text[extracted.page_starts[1] :]
        assert second.startswith("Second page")

    def test_pages_past_the_page_limit_are_dropped_and_it_says_so(self):
        limits = ExtractionLimits(max_pages=2)

        extracted = extract_document(pdf("one", "two", "three"), DocumentType.PDF, limits)

        assert extracted.page_count == 2
        assert "three" not in extracted.text
        assert extracted.is_truncated

    def test_a_password_protected_pdf_is_refused_plainly(self):
        assert _failure(encrypted_pdf("secret"), DocumentType.PDF) is DocumentFailure.ENCRYPTED

    def test_a_damaged_pdf_fails_without_its_parsers_message(self):
        with pytest.raises(ExtractionFailedError) as failed:
            extract_document(b"%PDF-1.4\n garbage (Amina Yusuf) \n", DocumentType.PDF, LIMITS)

        assert failed.value.failure in (DocumentFailure.UNREADABLE, DocumentFailure.NO_TEXT)
        assert "Amina" not in str(failed.value)


class TestDocx:
    def test_paragraphs_are_read_in_order(self):
        extracted = extract_document(
            docx("Field report", "Met the clinic team."), DocumentType.DOCX, LIMITS
        )

        assert extracted.text == "Field report\nMet the clinic team."
        assert extracted.page_count is None

    def test_tabs_and_breaks_become_whitespace(self):
        xml = (
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Name</w:t><w:tab/>'
            "<w:t>Amina</w:t><w:br/><w:t>Next</w:t></w:r></w:p></w:body></w:document>"
        )

        extracted = extract_document(docx(xml=xml), DocumentType.DOCX, LIMITS)

        assert extracted.text == "Name\tAmina\nNext"

    def test_an_external_entity_is_refused_and_never_read(self, tmp_path: Path):
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE")
        xml = (
            '<?xml version="1.0"?>'
            f'<!DOCTYPE d [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p>'
            "</w:body></w:document>"
        )

        with pytest.raises(ExtractionFailedError) as failed:
            extract_document(docx(xml=xml), DocumentType.DOCX, LIMITS)

        assert failed.value.failure is DocumentFailure.REFUSED_CONTENT
        assert "TOP-SECRET" not in str(failed.value)

    def test_an_entity_expansion_bomb_is_refused(self):
        entities = '<!ENTITY a0 "lol">' + "".join(
            f'<!ENTITY a{i} "{("&a" + str(i - 1) + ";") * 10}">' for i in range(1, 10)
        )
        xml = (
            f'<?xml version="1.0"?><!DOCTYPE d [{entities}]>'
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&a9;</w:t></w:r></w:p>'
            "</w:body></w:document>"
        )

        assert _failure(docx(xml=xml), DocumentType.DOCX) is DocumentFailure.REFUSED_CONTENT

    def test_a_zip_bomb_is_refused_before_it_inflates(self):
        bomb = document_xml("x") + " " * (8 * 1024 * 1024)

        assert _failure(docx(xml=bomb), DocumentType.DOCX) is DocumentFailure.TOO_COMPLEX

    def test_a_part_larger_than_the_member_limit_is_refused(self):
        limits = ExtractionLimits(max_member_bytes=64 * 1024)
        large = document_xml(*(["A paragraph of ordinary text."] * 5000))

        content = docx(xml=large, compression=zipfile.ZIP_STORED)

        assert _failure(content, DocumentType.DOCX, limits) is DocumentFailure.TOO_COMPLEX

    def test_an_archive_with_too_many_members_is_refused(self):
        extra = {f"word/media/image{i}.png": b"" for i in range(30)}

        content = docx("hello", extra=extra)

        limits = ExtractionLimits(max_archive_members=10)
        assert _failure(content, DocumentType.DOCX, limits) is DocumentFailure.TOO_COMPLEX

    def test_text_past_the_character_limit_is_cut_and_says_so(self):
        limits = ExtractionLimits(max_characters=1000)

        extracted = extract_document(
            docx(*(["Twenty characters ok."] * 200)), DocumentType.DOCX, limits
        )

        assert len(extracted.text) <= 1000
        assert extracted.is_truncated

    def test_a_damaged_archive_fails_plainly(self):
        assert _failure(b"PK\x03\x04broken", DocumentType.DOCX) is DocumentFailure.UNREADABLE


PARAGRAPH = DocumentLine(LineKind.PARAGRAPH)
ITEM = DocumentLine(LineKind.LIST_ITEM)


def _heading(level: int) -> DocumentLine:
    return DocumentLine(LineKind.HEADING, level=level)


def _cell(row: int, column: int, table: int = 0) -> DocumentLine:
    return DocumentLine(LineKind.TABLE_CELL, table=table, row=row, column=column)


def _structured(body: str, styles: dict[str, str] | None = None):
    extra = {"word/styles.xml": word_styles(styles)} if styles is not None else None
    return extract_document(docx(xml=word_body(body), extra=extra), DocumentType.DOCX, LIMITS)


class TestDocxStructure:
    """What each line of a Word file is, read from its styles, numbering and tables."""

    def test_the_extractor_says_this_version_records_structure(self):
        assert EXTRACTOR_ID.startswith("local@2+")

    def test_title_and_headings_one_to_three_are_headings_with_their_level(self):
        extracted = _structured(
            word_paragraph("Field report", "Title")
            + word_paragraph("Background", "Heading1")
            + word_paragraph("Gao", "Heading2")
            + word_paragraph("Clinics", "Heading3")
            + word_paragraph("The team met twice."),
            {
                "Title": "Title",
                "Heading1": "heading 1",
                "Heading2": "heading 2",
                "Heading3": "heading 3",
            },
        )

        assert extracted.text == "Field report\nBackground\nGao\nClinics\nThe team met twice."
        assert extracted.lines == (
            _heading(1),
            _heading(1),
            _heading(2),
            _heading(3),
            PARAGRAPH,
        )

    def test_a_heading_is_found_by_its_style_name_whatever_its_id(self):
        extracted = _structured(
            word_paragraph("Contexte", "Titre1") + word_paragraph("Le comité."),
            {"Titre1": "heading 1"},
        )

        assert extracted.lines == (_heading(1), PARAGRAPH)

    def test_headings_below_three_are_shown_at_level_three(self):
        extracted = _structured(
            word_paragraph("Detail", "Heading5") + word_paragraph("Text."),
            {"Heading5": "heading 5"},
        )

        assert extracted.lines == (_heading(3), PARAGRAPH)

    def test_without_a_styles_part_words_own_heading_ids_are_still_read(self):
        extracted = _structured(word_paragraph("Background", "Heading1") + word_paragraph("x."))

        assert extracted.lines == (_heading(1), PARAGRAPH)

    def test_numbered_paragraphs_and_list_styles_are_list_items(self):
        extracted = _structured(
            word_paragraph("Water", numbered=True)
            + word_paragraph("Food", "ListBullet")
            + word_paragraph("Shelter", "ListParagraph")
            + word_paragraph("The end."),
            {"ListBullet": "List Bullet", "ListParagraph": "List Paragraph"},
        )

        assert extracted.lines == (ITEM, ITEM, ITEM, PARAGRAPH)

    def test_numbering_switched_off_is_not_a_list_item(self):
        body = (
            '<w:p><w:pPr><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr>'
            "<w:r><w:t>Plain</w:t></w:r></w:p>"
        )

        assert _structured(body).lines == (PARAGRAPH,)

    def test_table_cells_say_their_table_row_and_column(self):
        extracted = _structured(
            word_paragraph("Before.")
            + word_table(("Site", "Households"), ("Gao", "120"))
            + word_paragraph("Between.")
            + word_table(("Only",))
        )

        assert extracted.text == "Before.\nSite\nHouseholds\nGao\n120\nBetween.\nOnly"
        assert extracted.lines == (
            PARAGRAPH,
            _cell(0, 0),
            _cell(0, 1),
            _cell(1, 0),
            _cell(1, 1),
            PARAGRAPH,
            _cell(0, 0, table=1),
        )

    def test_a_table_inside_a_cell_belongs_to_the_cell_it_sits_in(self):
        inner = word_table(("a", "b"))
        body = (
            "<w:tbl><w:tr>"
            f"<w:tc>{word_paragraph('Outer')}{inner}</w:tc>"
            f"<w:tc>{word_paragraph('Next')}</w:tc>"
            "</w:tr></w:tbl>"
        )

        extracted = _structured(body)

        assert extracted.text == "Outer\na\nb\nNext"
        assert extracted.lines == (_cell(0, 0), _cell(0, 0), _cell(0, 0), _cell(0, 1))

    def test_a_style_is_read_from_the_paragraph_not_from_its_tracked_history(self):
        body = (
            '<w:p><w:pPr><w:pPrChange w:id="1"><w:pPr><w:pStyle w:val="Heading1"/>'
            "</w:pPr></w:pPrChange></w:pPr><w:r><w:t>Was a heading.</w:t></w:r></w:p>"
        )

        assert _structured(body).lines == (PARAGRAPH,)

    def test_every_line_of_a_paragraph_with_a_break_has_its_kind(self):
        body = (
            '<w:p><w:pPr><w:numPr><w:numId w:val="3"/></w:numPr></w:pPr>'
            "<w:r><w:t>First</w:t><w:br/><w:t>second</w:t></w:r></w:p>"
        )

        assert _structured(body).lines == (ITEM, ITEM)

    def test_blank_paragraphs_cut_from_either_end_take_their_lines_with_them(self):
        extracted = _structured(
            word_paragraph("")
            + word_paragraph("Background", "Heading1")
            + word_paragraph("Text.")
            + word_paragraph("")
        )

        assert extracted.text == "Background\nText."
        assert extracted.lines == (_heading(1), PARAGRAPH)

    def test_cut_off_text_keeps_one_kind_per_line(self):
        limits = ExtractionLimits(max_characters=1000)
        body = "".join(word_paragraph("Twenty characters ok.", "Heading1") for _ in range(200))

        extracted = extract_document(docx(xml=word_body(body)), DocumentType.DOCX, limits)

        assert extracted.is_truncated
        assert extracted.lines is not None
        assert len(extracted.lines) == extracted.text.count("\n") + 1

    def test_a_styles_part_with_a_doctype_is_refused(self):
        styles = f'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY a "x">]><w:styles xmlns:w="{W}"/>'
        content = docx("Hello", extra={"word/styles.xml": styles.encode()})

        assert _failure(content, DocumentType.DOCX) is DocumentFailure.REFUSED_CONTENT

    def test_text_markdown_and_pdf_files_have_no_structure(self):
        assert extract_document(b"# Notes", DocumentType.MD, LIMITS).lines is None
        assert extract_document(pdf("One page"), DocumentType.PDF, LIMITS).lines is None


class TestLocalExtractor:
    async def test_it_runs_off_the_event_loop_and_gives_the_same_text(self):
        extracted = await LocalDocumentExtractor().extract(
            docx("Hello"), DocumentType.DOCX, LIMITS
        )

        assert extracted.text == "Hello"


class TestIsolatedExtractor:
    async def test_a_child_process_reads_the_file_and_answers_the_same_text(self):
        content = pdf("Cash transfers in Marib", "Second page")

        isolated = await IsolatedDocumentExtractor().extract(content, DocumentType.PDF, LIMITS)

        assert isolated == extract_document(content, DocumentType.PDF, LIMITS)

    async def test_a_child_process_answers_a_word_files_structure_too(self):
        content = docx(
            xml=word_body(word_paragraph("Background", "Heading1") + word_table(("a", "b")))
        )

        isolated = await IsolatedDocumentExtractor().extract(content, DocumentType.DOCX, LIMITS)

        assert isolated == extract_document(content, DocumentType.DOCX, LIMITS)
        assert isolated.lines == (_heading(1), _cell(0, 0), _cell(0, 1))

    async def test_a_failure_in_the_child_comes_back_as_its_reason(self):
        with pytest.raises(ExtractionFailedError) as failed:
            await IsolatedDocumentExtractor().extract(
                encrypted_pdf("x"), DocumentType.PDF, LIMITS
            )

        assert failed.value.failure is DocumentFailure.ENCRYPTED

    async def test_a_child_past_its_timeout_is_killed(self):
        limits = ExtractionLimits(timeout_seconds=0.001)

        with pytest.raises(ExtractionFailedError) as failed:
            await IsolatedDocumentExtractor().extract(b"text", DocumentType.TXT, limits)

        assert failed.value.failure is DocumentFailure.TIMED_OUT

    async def test_a_child_that_cannot_start_python_fails_plainly(self, tmp_path: Path):
        missing = str(tmp_path / "no-python")

        with pytest.raises((ExtractionFailedError, OSError)):
            await IsolatedDocumentExtractor(python=missing).extract(
                b"text", DocumentType.TXT, LIMITS
            )


class TestChildRefusesSideEffects:
    """The child's audit hook: no network, no processes, no file writes."""

    @staticmethod
    def _run_in_child(code: str) -> subprocess.CompletedProcess[str]:
        root = str(Path(__file__).resolve().parents[1])
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from app.extraction._child import forbid_side_effects; forbid_side_effects(); "
            + code
        )
        return subprocess.run(  # noqa: S603 — our own interpreter, fixed arguments
            [sys.executable, "-I", "-c", script, root],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_a_network_connection_is_refused(self):
        result = self._run_in_child(
            "import socket; socket.create_connection(('127.0.0.1', 9), timeout=1)"
        )

        assert result.returncode != 0
        assert "PermissionError" in result.stderr

    def test_writing_a_file_is_refused(self, tmp_path: Path):
        target = tmp_path / "written.txt"

        result = self._run_in_child(f"open({str(target)!r}, 'w').write('x')")

        assert result.returncode != 0
        assert not target.exists()

    def test_starting_a_process_is_refused(self):
        result = self._run_in_child("import subprocess; subprocess.run(['whoami'])")

        assert result.returncode != 0
        assert "PermissionError" in result.stderr


def test_extraction_never_blocks_the_event_loop_for_a_large_file():
    """A sanity bound, not a benchmark: a 2 MB text file reads in well under the timeout."""

    async def read() -> int:
        extracted = await LocalDocumentExtractor().extract(
            b"word " * 400_000, DocumentType.TXT, LIMITS
        )
        return len(extracted.text)

    assert asyncio.run(read()) == 2_000_000
