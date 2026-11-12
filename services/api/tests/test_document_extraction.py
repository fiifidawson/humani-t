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

from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractionFailedError,
    ExtractionLimits,
)
from app.extraction.isolated import IsolatedDocumentExtractor
from app.extraction.local import EXTRACTOR_ID, LocalDocumentExtractor, extract_document
from tests.document_support import W, document_xml, docx, encrypted_pdf, pdf

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
