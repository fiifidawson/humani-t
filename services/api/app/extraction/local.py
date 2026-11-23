"""Text out of plain text, Markdown, PDF (pypdf) and DOCX (`zipfile`, `expat`), within limits.

DOCX sizes are checked before and while inflating, because declared sizes can lie, and
any document type declaration is refused, which rules out XXE and entity bombs. A DOCX
also says what each line is (heading, list item, table cell), from its styles, numbering
and tables; nothing else of its formatting is read. Text in images is not read until OCR
is added.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO
from xml.parsers import expat

import pypdf
from pypdf import PdfReader

from app.domain.document_lines import MAX_HEADING_LEVEL, DocumentLine, LineKind
from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractedDocument,
    ExtractionFailedError,
    ExtractionLimits,
)

NAME = "local"
# Bump whenever what is extracted changes: documents record the version.
VERSION = "2"
EXTRACTOR_ID = f"{NAME}@{VERSION}+pypdf-{pypdf.__version__}"

# pypdf warns about malformed objects through `logging`. Those lines are noise
# in the operator's log and, for a crafted file, attacker-chosen text.
logging.getLogger("pypdf").setLevel(logging.ERROR)

_READ_BYTES = 64 * 1024
# Small parts legitimately compress very well (a part of repeated tags), so the
# ratio is only held against parts past this size.
_RATIO_FLOOR_BYTES = 1024 * 1024
_DOCX_STYLES = "word/styles.xml"
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


def _result(
    pages: _Pages, *, is_paged: bool, lines: list[DocumentLine] | None = None
) -> ExtractedDocument:
    text = pages.text()
    if not text.strip():
        raise ExtractionFailedError(DocumentFailure.NO_TEXT)
    return ExtractedDocument(
        text=text,
        page_starts=tuple(pages.starts) or (0,),
        is_truncated=pages.is_truncated,
        extractor=EXTRACTOR_ID,
        is_paged=is_paged,
        # Text cut at the character limit takes the kinds of its lost lines with it.
        lines=None if lines is None else tuple(lines[: text.count("\n") + 1]),
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


_PARAGRAPH = DocumentLine(LineKind.PARAGRAPH)
_LIST_ITEM = DocumentLine(LineKind.LIST_ITEM)
_HEADING_STYLE = re.compile(r"heading ?([1-9])")


def _style_kind(name: str) -> DocumentLine | None:
    """Built-in style names are English in every copy of Word, whatever their ids."""
    if name == "title":
        return DocumentLine(LineKind.HEADING, level=1)
    if heading := _HEADING_STYLE.fullmatch(name):
        level = min(int(heading.group(1)), MAX_HEADING_LEVEL)
        return DocumentLine(LineKind.HEADING, level=level)
    if name.startswith("list"):
        return _LIST_ITEM
    return None


class _DocxStyles:
    """Paragraph style ids and their names, from `styles.xml`."""

    is_full = False

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self._style_id: str | None = None

    def start(self, name: str, attributes: dict[str, str]) -> None:
        namespace, _, local = name.rpartition(" ")
        if namespace not in _WORD_NAMESPACES:
            return
        if local == "style":
            self._style_id = attributes.get(f"{namespace} styleId")
        elif local == "name" and self._style_id is not None:
            self.names[self._style_id] = attributes.get(f"{namespace} val", "").lower()

    def end(self, name: str) -> None:
        if name.rpartition(" ")[2] == "style":
            self._style_id = None

    def characters(self, _data: str) -> None:
        return None


@dataclass(slots=True)
class _Marks:
    style_id: str | None = None
    is_numbered: bool = False


class _DocxText:
    """Paragraph text from WordprocessingML events: `w:t` runs, tabs and breaks.

    Each paragraph's kind comes from its own `w:pPr` (not from a tracked change's old one)
    and from the outermost table it sits in: a table in a cell stays part of that cell.
    """

    def __init__(self, pages: _Pages, styles: dict[str, str]) -> None:
        self._pages = pages
        self._styles = styles
        self._paragraph: list[str] = []
        self._paragraphs: list[str] = []
        self._kinds: list[DocumentLine] = []
        self._in_text = False
        self._length = 0
        self._max = pages._max
        self._elements: list[str] = []
        self._marks: list[_Marks] = []
        self._table_depth = 0
        self._table = self._row = self._column = -1

    @property
    def is_full(self) -> bool:
        return self._length > self._max

    def start(self, name: str, attributes: dict[str, str]) -> None:
        namespace, _, local = name.rpartition(" ")
        if namespace not in _WORD_NAMESPACES:
            return
        path = self._elements[-3:]
        self._elements.append(local)
        match local:
            case "t":
                self._in_text = True
            case "tab":
                self._append("\t")
            case "br" | "cr":
                self._append("\n")
            case "p":
                self._marks.append(_Marks())
            case "pStyle" if path[-2:] == ["p", "pPr"]:
                self._marks[-1].style_id = attributes.get(f"{namespace} val")
            case "numId" if path == ["p", "pPr", "numPr"]:
                self._marks[-1].is_numbered = attributes.get(f"{namespace} val") != "0"
            case "tbl":
                self._table_depth += 1
                if self._table_depth == 1:
                    self._table += 1
                    self._row = -1
            case "tr" if self._table_depth == 1:
                self._row += 1
                self._column = -1
            case "tc" if self._table_depth == 1:
                self._column += 1
            case _:
                pass

    def end(self, name: str) -> None:
        namespace, _, local = name.rpartition(" ")
        if namespace not in _WORD_NAMESPACES:
            return
        self._elements.pop()
        if local == "t":
            self._in_text = False
        elif local == "p":
            self._close_paragraph(self._marks.pop())
        elif local == "tbl":
            self._table_depth -= 1

    def characters(self, data: str) -> None:
        if self._in_text:
            self._append(data)

    def _append(self, data: str) -> None:
        self._paragraph.append(data)
        self._length += len(data)

    def _close_paragraph(self, marks: _Marks) -> None:
        self._paragraphs.append("".join(self._paragraph))
        self._kinds.append(self._kind(marks))
        self._paragraph = []
        self._length += 1

    def _kind(self, marks: _Marks) -> DocumentLine:
        if self._table_depth > 0:
            return DocumentLine(
                LineKind.TABLE_CELL,
                table=self._table,
                row=max(self._row, 0),
                column=max(self._column, 0),
            )
        style = marks.style_id
        found = None if style is None else _style_kind(self._styles.get(style, style).lower())
        if found is not None and found.kind is LineKind.HEADING:
            return found
        return _LIST_ITEM if marks.is_numbered else found or _PARAGRAPH

    def finish(self) -> list[DocumentLine]:
        """Add the text to the pages; return each of its lines' kinds."""
        if self._paragraph:
            self._close_paragraph(self._marks[-1] if self._marks else _Marks())
        cleaned = [_clean(p) for p in self._paragraphs]
        lines = [
            kind
            for paragraph, kind in zip(cleaned, self._kinds, strict=True)
            for _ in range(paragraph.count("\n") + 1)
        ]
        text = "\n".join(cleaned)
        leading = len(text) - len(text.lstrip("\n"))
        self._pages.add(text.strip("\n"))
        self._paragraphs, self._paragraph, self._kinds = [], [], []
        return lines[leading:]


def _refuse(*_: object) -> int:
    raise _RefusedContentError


def _parse_part(
    stream: IO[bytes],
    compressed_size: int,
    limits: ExtractionLimits,
    text: _DocxText | _DocxStyles,
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


def _read_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    limits: ExtractionLimits,
    reader: _DocxText | _DocxStyles,
) -> None:
    if member.flag_bits & 0x1:
        raise ExtractionFailedError(DocumentFailure.ENCRYPTED)
    if member.file_size > limits.max_member_bytes or (
        member.file_size > _RATIO_FLOOR_BYTES
        and member.file_size > max(member.compress_size, 1) * limits.max_compression_ratio
    ):
        raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX)
    with archive.open(member) as stream:
        _parse_part(stream, member.compress_size, limits, reader)


def _docx(content: bytes, limits: ExtractionLimits) -> ExtractedDocument:
    pages = _Pages(limits.max_characters)
    styles = _DocxStyles()
    text = _DocxText(pages, styles.names)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > limits.max_archive_members:
                raise ExtractionFailedError(DocumentFailure.TOO_COMPLEX)
            by_name = {m.filename: m for m in members}
            if (member := by_name.get(_DOCX_STYLES)) is not None:
                _read_member(archive, member, limits, styles)
            for name in _DOCX_PARTS:
                member = by_name.get(name)
                if member is None:
                    continue
                _read_member(archive, member, limits, text)
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
    lines = text.finish()
    if text.is_full:
        pages.is_truncated = True
    return _result(pages, is_paged=False, lines=lines)


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

    The timeout stops the wait, not the work: a thread cannot be killed.
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
