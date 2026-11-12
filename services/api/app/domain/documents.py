"""A team's own documents: what may be uploaded, where each stands, what becomes evidence.

Reading, keeping and scanning files, and finding personal data, live in their own seams.
Raw text never appears in a record here: every stored text is sealed.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Protocol, cast
from uuid import UUID

from app.domain.masking import (
    PLACEHOLDER,
    STRUCTURED_ENTITY_TYPES,
    EntityType,
    MaskedText,
    MaskSpan,
    apply_spans,
    spans_from_json,
    spans_to_json,
    user_source,
)
from app.domain.screening import Passage, PassageSourceKind

MAX_FILENAME_LENGTH = 200
MAX_REASON_LENGTH = 200
MAX_USER_SPAN_LENGTH = 500
MAX_PROCESSING_ATTEMPTS = 3
PASSAGE_CHARACTERS = 1200

_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
# Characters Windows refuses in a file name. The name is only ever shown (files
# are stored by id), but a name that cannot be saved back is a poor surprise.
_UNSAFE_NAME_CHARACTERS = re.compile(r'[<>:"|?*]')
_TEXT_CONTROLS_ALLOWED = frozenset("\t\n\r\f")


class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MD = "md"


ACCEPTED_TYPES: tuple[DocumentType, ...] = tuple(DocumentType)
_MARKDOWN_EXTENSIONS = (".md", ".markdown")
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_REQUIRED_PARTS = frozenset({"[Content_Types].xml", "word/document.xml"})
# A macro-enabled document (.docm) renamed .docx. Macros never run here, but the
# type we accept is DOCX, and a file that is not what it says is refused.
_DOCX_MACRO_PART = "word/vbaProject.bin"


class UploadRefusal(StrEnum):
    """Why an upload was refused before anything was stored."""

    EMPTY = "empty"
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    QUOTA_EXCEEDED = "quota_exceeded"
    UPLOADS_DISABLED = "uploads_disabled"


class DocumentRefusedError(Exception):
    """An upload that cannot be accepted. The message is safe to show."""

    def __init__(self, reason: UploadRefusal, message: str) -> None:
        super().__init__(message)
        self.reason = reason


_UNSUPPORTED = (
    "This kind of file isn't accepted. Upload a PDF, a Word document (.docx), "
    "or plain text or Markdown in UTF-8."
)


def detect_document_type(content: bytes, filename: str) -> DocumentType:
    """Recognise the file from its bytes, never from the name or the browser's claim.

    The name decides only between plain text and Markdown, which are read the
    same way, so a misleading name can never choose a parser.
    """
    if not content:
        raise DocumentRefusedError(UploadRefusal.EMPTY, "The file is empty.")
    if content.startswith(_PDF_MAGIC):
        return DocumentType.PDF
    if content.startswith(_ZIP_MAGIC):
        if _is_docx(content):
            return DocumentType.DOCX
        raise DocumentRefusedError(UploadRefusal.UNSUPPORTED_TYPE, _UNSUPPORTED)
    if _is_utf8_text(content):
        if filename.lower().rstrip().endswith(_MARKDOWN_EXTENSIONS):
            return DocumentType.MD
        return DocumentType.TXT
    raise DocumentRefusedError(UploadRefusal.UNSUPPORTED_TYPE, _UNSUPPORTED)


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError, EOFError):
        return False
    return names >= _DOCX_REQUIRED_PARTS and _DOCX_MACRO_PART not in names


def _is_utf8_text(content: bytes) -> bool:
    if b"\x00" in content:
        return False
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return not any(
        unicodedata.category(ch) == "Cc" and ch not in _TEXT_CONTROLS_ALLOWED for ch in text
    )


def safe_filename(name: str) -> str:
    """The name to show for an upload: its last path part, without hidden or unsafe characters.

    Files are stored by id, so the name is never a path. Cleaning it stops it disguising its
    own text (bidirectional overrides) or breaking a log line.
    """
    base = re.split(r"[/\\]", name)[-1]
    base = unicodedata.normalize("NFC", base)
    visible = "".join(
        ch
        for ch in base
        if ch not in _BIDI_CONTROLS and unicodedata.category(ch) not in _HIDDEN_CATEGORIES
    )
    cleaned = " ".join(_UNSAFE_NAME_CHARACTERS.sub("_", visible).split()).strip(" .")
    if len(cleaned) > MAX_FILENAME_LENGTH:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and stem and len(extension) <= 10:
            keep = MAX_FILENAME_LENGTH - len(extension) - 1
            cleaned = f"{stem[:keep].rstrip(' .')}.{extension}"
        else:
            cleaned = cleaned[:MAX_FILENAME_LENGTH].rstrip(" .")
    return cleaned or "document"


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """What the operator allows: sizes, quotas, pace, and whether uploads are on at all.

    `disabled_reason` is shown to people as it is, so it says what the operator
    must do, never an internal detail.
    """

    max_file_bytes: int
    max_documents: int
    max_project_bytes: int
    uploads_per_hour: int
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_documents",
            "max_project_bytes",
            "uploads_per_hour",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"UploadPolicy.{name} must be positive")
        if self.max_file_bytes > self.max_project_bytes:
            raise ValueError("UploadPolicy.max_file_bytes must not exceed max_project_bytes")

    @property
    def is_enabled(self) -> bool:
        return self.disabled_reason is None

    def check_size(self, size: int) -> None:
        if size > self.max_file_bytes:
            raise DocumentRefusedError(
                UploadRefusal.TOO_LARGE,
                f"The file is larger than this deployment accepts "
                f"({self.max_file_bytes // (1024 * 1024)} MiB).",
            )

    def check_quota(self, usage: DocumentUsage, size: int) -> None:
        if usage.documents + 1 > self.max_documents:
            raise DocumentRefusedError(
                UploadRefusal.QUOTA_EXCEEDED,
                f"This project already has {usage.documents} documents, the most it can "
                "hold. Delete one before uploading another.",
            )
        if usage.size_bytes + size > self.max_project_bytes:
            raise DocumentRefusedError(
                UploadRefusal.QUOTA_EXCEEDED,
                "This project's documents would take more space than it is allowed. "
                "Delete a document before uploading another.",
            )


@dataclass(frozen=True, slots=True)
class DocumentUsage:
    documents: int
    size_bytes: int


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    SCANNING = "scanning"
    EXTRACTING = "extracting"
    MASKING = "masking"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


PROCESSING_STATUSES = frozenset(
    {DocumentStatus.SCANNING, DocumentStatus.EXTRACTING, DocumentStatus.MASKING}
)
_CORRECTABLE = frozenset({DocumentStatus.AWAITING_REVIEW, DocumentStatus.APPROVED})


class DocumentFailure(StrEnum):
    """Why processing stopped. Each has a fixed message: no file content ever reaches one."""

    MALWARE_FOUND = "malware_found"
    SCAN_FAILED = "scan_failed"
    UNREADABLE = "unreadable"
    ENCRYPTED = "encrypted"
    NO_TEXT = "no_text"
    TOO_COMPLEX = "too_complex"
    REFUSED_CONTENT = "refused_content"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


FAILURE_MESSAGES: Mapping[DocumentFailure, str] = MappingProxyType(
    {
        DocumentFailure.MALWARE_FOUND: (
            "The malware scanner flagged this file, so it was deleted unread."
        ),
        DocumentFailure.SCAN_FAILED: (
            "The malware scanner could not check this file, so it was not read."
        ),
        DocumentFailure.UNREADABLE: "The file could not be read. It may be damaged.",
        DocumentFailure.ENCRYPTED: (
            "The file is password-protected. Remove the password and upload it again."
        ),
        DocumentFailure.NO_TEXT: (
            "No text was found. A scanned document needs text recognition (OCR) first."
        ),
        DocumentFailure.TOO_COMPLEX: (
            "The file is too large or too deeply packed to read safely."
        ),
        DocumentFailure.REFUSED_CONTENT: (
            "The file contains structures that are refused for safety (such as external "
            "references)."
        ),
        DocumentFailure.TIMED_OUT: "Reading the file took too long, so it was stopped.",
        DocumentFailure.INTERRUPTED: (
            "Processing was interrupted several times. Delete the file and upload it again."
        ),
    }
)


class DocumentStateError(Exception):
    """The document is not in a state that allows this. The message is safe to show."""


def _require_aware(owner: str, **values: datetime | None) -> None:
    for name, value in values.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")


def _frozen_counts(counts: Mapping[str, int]) -> Mapping[str, int]:
    if any(n < 1 for n in counts.values()):
        raise ValueError("Document.masked_counts must be positive")
    return MappingProxyType(dict(sorted(counts.items())))


@dataclass(frozen=True, slots=True)
class Document:
    """A file a team uploaded to a project, and where it stands.

    `number` gives the tag a brief cites it by (`D3`), never reused within the project.
    `mask_version` counts maskings, and an approval names the version it approved.
    """

    id: UUID
    project_id: UUID
    number: int
    name_sealed: bytes
    file_type: DocumentType
    size_bytes: int
    status: DocumentStatus
    uploaded_by: UUID
    uploaded_at: datetime
    failure: DocumentFailure | None = None
    scanned_with: str | None = None
    extracted_with: str | None = None
    page_count: int | None = None
    character_count: int | None = None
    is_truncated: bool = False
    masked_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    mask_version: int = 0
    claimed_by: str | None = None
    lease_until: datetime | None = None
    attempts: int = 0
    version: int = 0

    def __post_init__(self) -> None:
        _require_aware("Document", uploaded_at=self.uploaded_at, lease_until=self.lease_until)
        if self.number < 1:
            raise ValueError("Document.number must count from 1")
        if self.size_bytes < 1:
            raise ValueError("Document.size_bytes must be positive")
        if not self.name_sealed:
            raise ValueError("Document.name_sealed must not be empty")
        if (self.failure is not None) != (self.status is DocumentStatus.FAILED):
            raise ValueError("Document.failure is set exactly when the status is failed")
        is_claimed = self.claimed_by is not None and self.lease_until is not None
        if is_claimed != (self.status in PROCESSING_STATUSES):
            raise ValueError("Document is claimed exactly while it is being processed")
        if self.mask_version < 0 or self.attempts < 0:
            raise ValueError("Document.mask_version and attempts must not be negative")
        object.__setattr__(self, "masked_counts", _frozen_counts(self.masked_counts))

    @property
    def tag(self) -> str:
        return document_tag(self.number)

    @property
    def is_processing(self) -> bool:
        return self.status in PROCESSING_STATUSES

    def _expect(self, *allowed: DocumentStatus) -> None:
        if self.status not in allowed:
            raise DocumentStateError(_STATE_MESSAGES.get(self.status, _STATE_DEFAULT))

    def claimed(self, worker_id: str, until: datetime) -> Document:
        self._expect(DocumentStatus.UPLOADED)
        return replace(
            self,
            status=DocumentStatus.SCANNING,
            claimed_by=worker_id,
            lease_until=until,
            attempts=self.attempts + 1,
        )

    def requeued(self) -> Document:
        self._expect(*PROCESSING_STATUSES)
        return replace(self, status=DocumentStatus.UPLOADED, claimed_by=None, lease_until=None)

    def scanned(self, scanner: str, until: datetime) -> Document:
        self._expect(DocumentStatus.SCANNING)
        return replace(
            self, status=DocumentStatus.EXTRACTING, scanned_with=scanner, lease_until=until
        )

    def extracted(self, extracted: ExtractedDocument, until: datetime) -> Document:
        self._expect(DocumentStatus.EXTRACTING)
        return replace(
            self,
            status=DocumentStatus.MASKING,
            extracted_with=extracted.extractor,
            page_count=extracted.page_count,
            character_count=len(extracted.text),
            is_truncated=extracted.is_truncated,
            lease_until=until,
        )

    def masked(self, counts: Mapping[str, int]) -> Document:
        self._expect(DocumentStatus.MASKING)
        return replace(
            self,
            status=DocumentStatus.AWAITING_REVIEW,
            masked_counts=counts,
            mask_version=1,
            claimed_by=None,
            lease_until=None,
        )

    def failed(self, failure: DocumentFailure) -> Document:
        self._expect(DocumentStatus.UPLOADED, *PROCESSING_STATUSES)
        return replace(
            self,
            status=DocumentStatus.FAILED,
            failure=failure,
            claimed_by=None,
            lease_until=None,
        )

    def renewed(self, until: datetime) -> Document:
        self._expect(*PROCESSING_STATUSES)
        return replace(self, lease_until=until)

    def corrected(self, counts: Mapping[str, int]) -> Document:
        """A person changed the masking: a new version, which needs a review again."""
        self._expect(*_CORRECTABLE)
        return replace(
            self,
            status=DocumentStatus.AWAITING_REVIEW,
            masked_counts=counts,
            mask_version=self.mask_version + 1,
        )

    def decided(self, decision: DocumentDecision, mask_version: int) -> Document:
        self._expect(DocumentStatus.AWAITING_REVIEW)
        if mask_version != self.mask_version:
            raise DocumentStateError(
                "The masking changed since you looked at it. Reload it and review again."
            )
        approved = decision is DocumentDecision.APPROVE
        return replace(
            self, status=DocumentStatus.APPROVED if approved else DocumentStatus.REJECTED
        )


_STATE_DEFAULT = "This document cannot be changed now."
_STATE_MESSAGES: Mapping[DocumentStatus, str] = MappingProxyType(
    {
        DocumentStatus.UPLOADED: "This document is still waiting to be read.",
        DocumentStatus.SCANNING: "This document is still being checked.",
        DocumentStatus.EXTRACTING: "This document is still being read.",
        DocumentStatus.MASKING: "This document is still being masked.",
        DocumentStatus.APPROVED: "This document's masking is already approved.",
        DocumentStatus.REJECTED: (
            "This document was rejected. Delete it and upload a corrected file."
        ),
        DocumentStatus.FAILED: "This document could not be processed.",
    }
)


def document_tag(number: int) -> str:
    """The tag a brief cites a team document by: `D1`, `D2`, …"""
    if number < 1:
        raise ValueError("a document tag needs a number counting from 1")
    return f"D{number}"


class DocumentDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DocumentReview:
    """One person's decision about one masked version of one document. Written once."""

    id: UUID
    document_id: UUID
    project_id: UUID
    decision: DocumentDecision
    mask_version: int
    decided_by: UUID
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_aware("DocumentReview", decided_at=self.decided_at)
        if self.mask_version < 1:
            raise ValueError("DocumentReview.mask_version must count from 1")


@dataclass(frozen=True, slots=True)
class DocumentText:
    """The extracted raw text, sealed, and where each page starts in it. Never modified."""

    document_id: UUID
    project_id: UUID
    raw_sealed: bytes
    page_starts: tuple[int, ...]
    extracted_at: datetime

    def __post_init__(self) -> None:
        _require_aware("DocumentText", extracted_at=self.extracted_at)
        if not self.raw_sealed:
            raise ValueError("DocumentText.raw_sealed must not be empty")
        _require_page_starts("DocumentText", self.page_starts)


@dataclass(frozen=True, slots=True)
class DocumentMask:
    """One masked version of a document: masked text and its details, both sealed.

    `masked_by` is `None` when the worker made it, else the person whose correction made it.
    """

    document_id: UUID
    project_id: UUID
    version: int
    masked_sealed: bytes
    detail_sealed: bytes
    masked_with: tuple[str, ...]
    masked_at: datetime
    masked_by: UUID | None = None

    def __post_init__(self) -> None:
        _require_aware("DocumentMask", masked_at=self.masked_at)
        if self.version < 1:
            raise ValueError("DocumentMask.version must count from 1")
        if not self.masked_sealed or not self.detail_sealed:
            raise ValueError("DocumentMask sealed fields must not be empty")
        if not self.masked_with:
            raise ValueError("DocumentMask.masked_with must name at least one detector")


def _require_page_starts(owner: str, page_starts: Sequence[int]) -> None:
    if not page_starts or page_starts[0] != 0:
        raise ValueError(f"{owner}.page_starts must start at 0")
    if any(b <= a for a, b in pairwise(page_starts)):
        raise ValueError(f"{owner}.page_starts must increase")


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """How much of a file is read, and the bounds that stop a hostile one.

    Text past the limits is dropped and flagged `is_truncated`. An archive past its bounds
    is refused, because a zip bomb is an attack, not a long document.
    """

    max_pages: int = 500
    max_characters: int = 2_000_000
    max_archive_members: int = 1000
    max_member_bytes: int = 64 * 1024 * 1024
    max_compression_ratio: int = 200
    timeout_seconds: float = 60

    def __post_init__(self) -> None:
        for name in (
            "max_pages",
            "max_characters",
            "max_archive_members",
            "max_member_bytes",
            "max_compression_ratio",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"ExtractionLimits.{name} must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("ExtractionLimits.timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """A file's text, and where each page starts in it (`(0,)` for unpaged formats)."""

    text: str
    page_starts: tuple[int, ...]
    is_truncated: bool
    extractor: str
    is_paged: bool = False

    def __post_init__(self) -> None:
        _require_page_starts("ExtractedDocument", self.page_starts)
        if len(self.page_starts) > 1 and not self.is_paged:
            raise ValueError("ExtractedDocument has pages only when it is paged")
        if max(self.page_starts) > len(self.text):
            raise ValueError("ExtractedDocument.page_starts must lie within the text")
        if not self.extractor.strip():
            raise ValueError("ExtractedDocument.extractor must not be blank")

    @property
    def page_count(self) -> int | None:
        """Pages for a paged format (PDF); `None` where a file has no fixed pages."""
        return len(self.page_starts) if self.is_paged else None


class ExtractionFailedError(Exception):
    """The file could not be read safely; `failure` says why, in a fixed message."""

    def __init__(self, failure: DocumentFailure) -> None:
        super().__init__(FAILURE_MESSAGES[failure])
        self.failure = failure


class ScanOutcome(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    NOT_SCANNED = "not_scanned"


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    """What the scanner said. `signature` names what it found, for the audit log only."""

    outcome: ScanOutcome
    scanner: str
    signature: str | None = None

    def __post_init__(self) -> None:
        if (self.signature is not None) != (self.outcome is ScanOutcome.INFECTED):
            raise ValueError("ScanVerdict.signature is set exactly when the file is infected")


class MalwareScanUnavailableError(Exception):
    """The scanner could not be asked. The file is not read; processing is tried again."""


class MalwareScanRefusedError(Exception):
    """The scanner refused this file (too large for it, for example). Not retried."""


@dataclass(frozen=True, slots=True)
class MaskExemption:
    """A mask a person removed, and why. Kept so the removal can be explained later."""

    start: int
    end: int
    entity_type: EntityType
    source: str
    removed_by: UUID
    reason: str

    def to_json(self) -> dict[str, str | int]:
        return {
            "start": self.start,
            "end": self.end,
            "entity_type": self.entity_type.value,
            "source": self.source,
            "removed_by": str(self.removed_by),
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, item: Mapping[str, object]) -> MaskExemption:
        start, end = item.get("start"), item.get("end")
        kind, source = item.get("entity_type"), item.get("source")
        by, reason = item.get("removed_by"), item.get("reason")
        if not (isinstance(start, int) and isinstance(end, int)):
            raise ValueError("MaskExemption offsets must be integers")
        if not all(isinstance(v, str) for v in (kind, source, by, reason)):
            raise ValueError("MaskExemption fields must be strings")
        return cls(start, end, EntityType(str(kind)), str(source), UUID(str(by)), str(reason))


def normalize_reason(reason: str) -> str:
    """One line saying why a mask was removed: required, short, free of hidden characters."""
    normalised = " ".join(reason.split())
    if not normalised:
        raise ValueError("The reason must not be blank")
    if len(normalised) > MAX_REASON_LENGTH:
        raise ValueError(f"The reason must be at most {MAX_REASON_LENGTH} characters")
    if any(
        ch in _BIDI_CONTROLS or unicodedata.category(ch) in _HIDDEN_CATEGORIES
        for ch in normalised
    ):
        raise ValueError("The reason must not contain control characters")
    return normalised


def user_span(
    raw: str, start: int, end: int, entity_type: EntityType, user_id: UUID
) -> MaskSpan:
    """A mask a person drew over `raw[start:end]`. Raise `ValueError` if it cannot be one."""
    if not 0 <= start < end <= len(raw):
        raise ValueError("The range to mask must lie within the text")
    if end - start > MAX_USER_SPAN_LENGTH:
        raise ValueError(f"A mask can cover at most {MAX_USER_SPAN_LENGTH} characters")
    if not raw[start:end].strip():
        raise ValueError("The range to mask must contain text")
    return MaskSpan(start, end, entity_type, user_source(user_id))


def removable_spans(spans: Sequence[MaskSpan], start: int, end: int) -> tuple[MaskSpan, ...]:
    """The spans exactly over `[start, end)` that a person may remove.

    Raise `LookupError` if none covers exactly that range, and `ValueError` if one may not
    be removed: a project rule, or a detector's structured identifier.
    """
    matching = tuple(s for s in spans if s.start == start and s.end == end)
    if not matching:
        raise LookupError("No mask covers exactly that range")
    for span in matching:
        if span.source.startswith("rule:"):
            raise ValueError(
                "A project masking rule made this mask; remove the rule to stop masking it"
            )
        if not span.source.startswith("user:") and span.entity_type in STRUCTURED_ENTITY_TYPES:
            raise ValueError(
                "Structured identifiers (emails, phone numbers, ID numbers…) stay masked"
            )
    return matching


@dataclass(frozen=True, slots=True)
class MaskedRegion:
    """One placeholder: where it sits in the raw and in the masked text, and what made it."""

    raw_start: int
    raw_end: int
    masked_start: int
    masked_end: int
    entity_type: EntityType
    sources: tuple[str, ...]


def masked_regions(masked: MaskedText) -> tuple[MaskedRegion, ...]:
    """Where each merged region of `masked.spans` landed in `masked.masked`.

    Overlapping spans merge exactly as `apply_spans` merges them, and each
    region is one placeholder, which is read back from the masked text itself.
    """
    regions: list[MaskedRegion] = []
    group: list[MaskSpan] = []
    shift = 0

    def close() -> None:
        nonlocal shift
        start, end = group[0].start, max(s.end for s in group)
        at = start + shift
        found = PLACEHOLDER.match(masked.masked, at)
        if found is None:
            raise ValueError("masked text does not match its spans")
        kind = EntityType(found.group(0)[1:].rsplit("_", 1)[0])
        sources = tuple(sorted({s.source for s in group}))
        regions.append(MaskedRegion(start, end, at, found.end(), kind, sources))
        shift += (found.end() - at) - (end - start)

    for span in sorted(masked.spans):
        if group and span.start >= max(s.end for s in group):
            close()
            group = []
        group.append(span)
    if group:
        close()
    return tuple(regions)


@dataclass(frozen=True, slots=True)
class MaskDetail:
    """What a masked version is made of, sealed as one value."""

    spans: tuple[MaskSpan, ...]
    exemptions: tuple[MaskExemption, ...]
    masked_page_starts: tuple[int, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "spans": spans_to_json(self.spans),
            "exemptions": [e.to_json() for e in self.exemptions],
            "masked_page_starts": list(self.masked_page_starts),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> MaskDetail:
        return cls(
            spans_from_json(_mappings(data.get("spans"))),
            tuple(MaskExemption.from_json(e) for e in _mappings(data.get("exemptions"))),
            _integers(data.get("masked_page_starts")),
        )


def _mappings(value: object) -> list[Mapping[str, object]]:
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(isinstance(item, dict) for item in items):
        raise ValueError("MaskDetail fields must be lists of objects")
    return [cast(Mapping[str, object], item) for item in items]


def _integers(value: object) -> tuple[int, ...]:
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(type(item) is int for item in items):
        raise ValueError("MaskDetail.masked_page_starts must be a list of integers")
    return tuple(cast(int, item) for item in items)


def remask(
    raw: str, spans: Sequence[MaskSpan], raw_page_starts: Sequence[int]
) -> tuple[MaskedText, tuple[int, ...]]:
    """Mask `raw` with `spans`, and say where each raw page start lands in the result."""
    masked = apply_spans(raw, spans)
    regions = masked_regions(masked)
    return masked, tuple(masked_position(regions, p) for p in raw_page_starts)


def masked_position(regions: Sequence[MaskedRegion], raw_position: int) -> int:
    """Where a raw offset lands in the masked text; inside a region, at its placeholder."""
    shift = 0
    for region in regions:
        if raw_position < region.raw_end:
            if raw_position > region.raw_start:
                return region.masked_start
            break
        shift = region.masked_end - region.raw_end
    return raw_position + shift


@dataclass(frozen=True, slots=True)
class _Chunk:
    start: int
    end: int
    text: str


def _chunks(text: str, size: int) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            # Prefer a paragraph break, then a line break, then a space. A
            # placeholder holds no whitespace, so none is ever cut in two.
            for breaker in ("\n\n", "\n", " "):
                cut = window.rfind(breaker)
                if cut > size // 3:
                    end = start + cut + len(breaker)
                    break
        piece = text[start:end]
        if piece.strip():
            chunks.append(_Chunk(start, end, piece.strip()))
        start = end
    return chunks


def _locator(chunk: _Chunk, page_starts: Sequence[int], is_paged: bool) -> str:
    if not is_paged:
        return f"characters {chunk.start}-{chunk.end}"
    first = bisect_right(page_starts, chunk.start)
    last = bisect_right(page_starts, max(chunk.start, chunk.end - 1))
    return f"page {first}" if first == last else f"pages {first}-{last}"


def document_passages(
    document: Document,
    masked_text: str,
    masked_page_starts: Sequence[int],
    size: int = PASSAGE_CHARACTERS,
) -> tuple[Passage, ...]:
    """Cut an approved document's masked text into passages, all tagged with its `D` tag.

    The locator says where: a page for a PDF, else a character range of the masked text.
    """
    is_paged = document.file_type is DocumentType.PDF
    return tuple(
        Passage(
            tag=document.tag,
            paper_id=document.id,
            source_kind=PassageSourceKind.DOCUMENT,
            text=chunk.text,
            locator=_locator(chunk, masked_page_starts, is_paged),
        )
        for chunk in _chunks(masked_text, size)
    )


class DocumentPassageSource[UnitOfWorkT](Protocol):
    """Where the evidence of a run reads the project's approved documents."""

    async def approved_passages(
        self, uow: UnitOfWorkT, project_id: UUID
    ) -> tuple[Passage, ...]:
        """Masked passages of every approved document of the project, by tag. Empty if none."""
        ...
