"""Text out of plain text, Markdown, PDF and DOCX, inside this machine and within limits.

- **Text and Markdown:** UTF-8, read with the standard library.
- **PDF:** pypdf, a pure-Python reader with no dependencies and no network
  code. It caps how much a compressed stream may inflate to; the page and
  character limits here cap the rest.
- **DOCX:** the standard library's `zipfile` and `expat`. Before a byte is
  inflated, the archive's member count, each part's declared size and its
  compression ratio are checked; while it inflates, the real bytes are
  counted against the same bounds, because declared sizes can lie. A part
  with a document type declaration is refused, which rules out external
  entities (XXE) and entity expansion bombs at the root: DOCX never needs one.

Text in images is not read: a scanned page or a photographed form has no text
here until OCR is added. The review screen says so.

`extract_document` is synchronous and pure (bytes in, text out). Production
runs it in a separate process (`isolated.py`); `LocalDocumentExtractor` runs it
in a thread, for tests and development, where a timeout cannot stop it early.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from collections.abc import Callable
from typing import IO
from xml.parsers import expat

import pypdf
from pypdf import PdfReader

from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractedDocument,
    ExtractionFailedError,
    ExtractionLimits,
)

NAME = "local"
# Bump whenever what is extracted changes: documents record the version.
VERSION = "1"
EXTRACTOR_ID = f"{NAME}@{VERSION}+pypdf-{pypdf.__version__}"

# pypdf warns about malformed objects through `logging`. Those lines are noise
# in the operator's log and, for a crafted file, attacker-chosen text.
logging.getLogger("pypdf").setLevel(logging.ERROR)

_READ_BYTES = 64 * 1024
# Small parts legitimately compress very well (a part of repeated tags), so the
# ratio is only held against parts past this size.
_RATIO_FLOOR_BYTES = 1024 * 1024
_DOCX_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
_WORD_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)
_LINE_BREAKS = re.compile(r"\r\n?")
# Control characters other than tab and newline: they break offsets in a
# browser and carry no text.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text: str) -> str:
    return _CONTROLS.sub("", _LINE_BREAKS.sub("\n", text))


class _Pages:
    """Pages joined by a blank line, cut off at the character limit."""

    def __init__(self, max_characters: int) -> None:
        self._max = max_characters
        self._parts: list[str] = []
        self.starts: list[int] = []
        self.length = 0
        self.is_truncated = False

    @property
    def is_full(self) -> bool:
        return self.is_truncated

    def add(self, text: str) -> None:
        separator = "\n\n" if self.starts else ""
        start = self.length + len(separator)
        room = self._max - start
        if room <= 0:
            self.is_truncated = True
            return
        if len(text) > room:
            text = text[:room]
            self.is_truncated = True
        self._parts.extend((separator, text))
        self.starts.append(start)
        self.length = start + len(text)

    def text(self) -> str:
        return "".join(self._parts)


def _result(pages: _Pages, *, is_paged: bool) -> ExtractedDocument:
    text = pages.text()
    if not text.strip():
        raise ExtractionFailedError(DocumentFailure.NO_TEXT)
    return ExtractedDocument(
        text=text,
        page_starts=tuple(pages.starts) or (0,),
        is_truncated=pages.is_truncated,
        extractor=EXTRACTOR_ID,
        is_paged=is_paged,
    )


def _plain_text(content: bytes, limits: ExtractionLimits) -> ExtractedDocument:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionFailedError(DocumentFailure.UNREADABLE) from exc
    pages = _Pages(limits.max_characters)
    pages.add(_clean(text))
    return _result(pages, is_paged=False)


def _pdf(content: bytes, limits: ExtractionLimits) -> ExtractedDocument:
    pages = _Pages(limits.max_characters)
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionFailedError(DocumentFailure.ENCRYPTED)
        for number, page in enumerate(reader.pages):
            if number >= limits.max_pages:
                pages.is_truncated = True
                break
            pages.add(_clean(page.extract_text() or ""))
            if pages.is_full:
                break
    except ExtractionFailedError:
        raise
    except (RecursionError, MemoryError) as exc:
        raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX) from exc
    # A crafted PDF can make the parser raise almost any exception type
    # (KeyError, struct.error, zlib.error…). Each is mapped to one fixed reason,
    # never passed on, because its message can quote the file.
    except Exception as exc:
        raise ExtractionFailedError(DocumentFailure.UNREADABLE) from exc
    return _result(pages, is_paged=True)


class _RefusedContentError(Exception):
    pass


class _DocxText:
    """Paragraph text from WordprocessingML events: `w:t` runs, tabs and breaks."""

    def __init__(self, pages: _Pages) -> None:
        self._pages = pages
        self._paragraph: list[str] = []
        self._paragraphs: list[str] = []
        self._in_text = False
        self._length = 0
        self._max = pages._max

    @property
    def is_full(self) -> bool:
        return self._length > self._max

    def start(self, name: str, _attributes: dict[str, str]) -> None:
        namespace, _, local = name.rpartition(" ")
        if namespace not in _WORD_NAMESPACES:
            return
        if local == "t":
            self._in_text = True
        elif local == "tab":
            self._append("\t")
        elif local in ("br", "cr"):
            self._append("\n")

    def end(self, name: str) -> None:
        namespace, _, local = name.rpartition(" ")
        if namespace not in _WORD_NAMESPACES:
            return
        if local == "t":
            self._in_text = False
        elif local == "p":
            self._paragraphs.append("".join(self._paragraph))
            self._paragraph = []
            self._length += 1

    def characters(self, data: str) -> None:
        if self._in_text:
            self._append(data)

    def _append(self, data: str) -> None:
        self._paragraph.append(data)
        self._length += len(data)

    def finish(self) -> None:
        if self._paragraph:
            self._paragraphs.append("".join(self._paragraph))
        text = "\n".join(p for p in self._paragraphs)
        self._pages.add(_clean(text).strip("\n"))
        self._paragraphs, self._paragraph = [], []


def _refuse(*_: object) -> int:
    raise _RefusedContentError


def _parse_part(
    stream: IO[bytes],
    compressed_size: int,
    limits: ExtractionLimits,
    text: _DocxText,
) -> None:
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    refuse: Callable[..., int] = _refuse
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.StartElementHandler = text.start
    parser.EndElementHandler = text.end
    parser.CharacterDataHandler = text.characters
    inflated = 0
    while chunk := stream.read(_READ_BYTES):
        inflated += len(chunk)
        if inflated > limits.max_member_bytes or (
            inflated > _RATIO_FLOOR_BYTES
            and inflated > max(compressed_size, 1) * limits.max_compression_ratio
        ):
            raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX)
        parser.Parse(chunk, False)
        if text.is_full:
            return
    parser.Parse(b"", True)


def _docx(content: bytes, limits: ExtractionLimits) -> ExtractedDocument:
    pages = _Pages(limits.max_characters)
    text = _DocxText(pages)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > limits.max_archive_members:
                raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX)
            by_name = {m.filename: m for m in members}
            for name in _DOCX_PARTS:
                member = by_name.get(name)
                if member is None:
                    continue
                if member.flag_bits & 0x1:
                    raise ExtractionFailedError(DocumentFailure.ENCRYPTED)
                if member.file_size > limits.max_member_bytes or (
                    member.file_size > _RATIO_FLOOR_BYTES
                    and member.file_size
                    > max(member.compress_size, 1) * limits.max_compression_ratio
                ):
                    raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX)
                with archive.open(member) as stream:
                    _parse_part(stream, member.compress_size, limits, text)
                if text.is_full:
                    break
    except ExtractionFailedError:
        raise
    except _RefusedContentError as exc:
        raise ExtractionFailedError(DocumentFailure.REFUSED_CONTENT) from exc
    except (RecursionError, MemoryError) as exc:
        raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX) from exc
    except (
        zipfile.BadZipFile,
        expat.ExpatError,
        EOFError,
        ValueError,
        NotImplementedError,
    ) as exc:
        raise ExtractionFailedError(DocumentFailure.UNREADABLE) from exc
    text.finish()
    if text.is_full:
        pages.is_truncated = True
    return _result(pages, is_paged=False)


def extract_document(
    content: bytes, file_type: DocumentType, limits: ExtractionLimits
) -> ExtractedDocument:
    """Read `content` as `file_type`; raise `ExtractionFailedError` if it is unsafe to read."""
    match file_type:
        case DocumentType.TXT | DocumentType.MD:
            return _plain_text(content, limits)
        case DocumentType.PDF:
            return _pdf(content, limits)
        case DocumentType.DOCX:
            return _docx(content, limits)


class LocalDocumentExtractor:
    """`extract_document` in a thread of this process. For tests and development.

    The timeout stops the wait, not the work: a thread cannot be killed, so a
    file that hangs the parser keeps a thread busy until it ends.
    `IsolatedDocumentExtractor` is the production choice for that reason.
    """

    @property
    def extractor_id(self) -> str:
        return EXTRACTOR_ID

    async def extract(
        self, content: bytes, file_type: DocumentType, limits: ExtractionLimits
    ) -> ExtractedDocument:
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                return await asyncio.to_thread(extract_document, content, file_type, limits)
        except TimeoutError as exc:
            raise ExtractionFailedError(DocumentFailure.TIMED_OUT) from exc
