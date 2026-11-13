"""Uploaded documents: the upload, the worker that reads and masks them, review, evidence.

`DocumentService` serves the routes; `DocumentProcessor` is the worker that scans,
extracts and masks; `DocumentEvidence` feeds approved masked passages to the brief. No
database connection is held across file I/O, scanning, extraction or masking. Raw text
goes only to the owner's review screen; a model sees only approved masked passages,
unless the project's masking is off (ADR 0014): then every ready document, as written
apart from the project's rule terms.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from app.application.accounts import Current
from app.application.audit import SYSTEM, AuditLog
from app.application.encryption import ScopeEncryption
from app.application.masking import MaskingRuleService
from app.application.membership import require_member
from app.application.runs import RunService
from app.domain.accounts import NotFoundError, ThrottledError
from app.domain.documents import (
    MAX_PROCESSING_ATTEMPTS,
    Document,
    DocumentDecision,
    DocumentFailure,
    DocumentMask,
    DocumentRefusedError,
    DocumentReview,
    DocumentStateError,
    DocumentStatus,
    DocumentText,
    DocumentUsage,
    ExtractionFailedError,
    ExtractionLimits,
    MalwareScanRefusedError,
    MalwareScanUnavailableError,
    MaskDetail,
    MaskedRegion,
    MaskExemption,
    ScanOutcome,
    UploadPolicy,
    UploadRefusal,
    detect_document_type,
    document_passages,
    masked_regions,
    normalize_reason,
    remask,
    removable_spans,
    safe_filename,
    user_span,
)
from app.domain.encryption import EncryptionContext, KeyDestroyedError
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.domain.masking import (
    DocumentAwaitingReview,
    EntityType,
    MaskedText,
    MaskingRule,
    MaskSpan,
    masking_fingerprint,
    rule_spans,
)
from app.domain.projects import Action
from app.domain.runs import GateDecision, GateKind, RunConflictError, RunState
from app.domain.screening import Passage
from app.extraction.base import DocumentExtractor
from app.files.base import FileStore, file_key
from app.malware.base import MalwareScanner
from app.privacy.base import Sanitizer
from app.ratelimit.base import RateLimiter
from app.signals.base import Signals
from app.storage.base import StorageProvider, UnitOfWork

DOCUMENTS_CHANNEL = "documents:queued"
# The stage that raises the privacy_review gate (`application/masking.py`).
MASK_STAGE = "mask"

# Table, column; the row is the document, and a masking is bound to its version
# too, so an older masking copied over a newer one fails to decrypt.
_NAME_PURPOSE = "documents.name"
_FILE_PURPOSE = "documents.file"
_RAW_PURPOSE = "document_texts.raw"
_MASKED_PURPOSE = "document_masks.masked"
_DETAIL_PURPOSE = "document_masks.detail"

_NO_PROJECT = "No such project."
_NO_DOCUMENT = "No such document."
_MASKING_CHANGED = "The masking changed since you looked at it. Reload it and review again."
_UPLOAD_WINDOW = timedelta(hours=1)
_RECOVERY_BATCH = 10

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _nothing() -> None:
    return None


class DocumentSeal:
    """The one place that knows how each document field is sealed and bound to its row."""

    def __init__(self, encryption: ScopeEncryption) -> None:
        self._encryption = encryption

    async def _seal(
        self, uow: UnitOfWork, project_id: UUID, purpose: str, row: str, plaintext: bytes
    ) -> bytes:
        context = EncryptionContext(project_id, purpose, row)
        return await self._encryption.encrypt(uow, context, plaintext)

    async def _open(
        self, uow: UnitOfWork, project_id: UUID, purpose: str, row: str, sealed: bytes
    ) -> bytes:
        context = EncryptionContext(project_id, purpose, row)
        return await self._encryption.decrypt(uow, context, sealed)

    async def seal_name(
        self, uow: UnitOfWork, project_id: UUID, document_id: UUID, name: str
    ) -> bytes:
        return await self._seal(uow, project_id, _NAME_PURPOSE, str(document_id), name.encode())

    async def name(self, uow: UnitOfWork, document: Document) -> str:
        opened = await self._open(
            uow, document.project_id, _NAME_PURPOSE, str(document.id), document.name_sealed
        )
        return opened.decode()

    async def seal_file(
        self, uow: UnitOfWork, project_id: UUID, document_id: UUID, content: bytes
    ) -> bytes:
        return await self._seal(uow, project_id, _FILE_PURPOSE, str(document_id), content)

    async def open_file(self, uow: UnitOfWork, document: Document, sealed: bytes) -> bytes:
        return await self._open(
            uow, document.project_id, _FILE_PURPOSE, str(document.id), sealed
        )

    async def seal_raw(self, uow: UnitOfWork, document: Document, raw: str) -> bytes:
        return await self._seal(
            uow, document.project_id, _RAW_PURPOSE, str(document.id), raw.encode()
        )

    async def open_raw(self, uow: UnitOfWork, text: DocumentText) -> str:
        opened = await self._open(
            uow, text.project_id, _RAW_PURPOSE, str(text.document_id), text.raw_sealed
        )
        return opened.decode()

    async def seal_mask(
        self,
        uow: UnitOfWork,
        document: Document,
        version: int,
        masked: MaskedText,
        detail: MaskDetail,
    ) -> tuple[bytes, bytes]:
        row = f"{document.id}:v{version}"
        masked_sealed = await self._seal(
            uow, document.project_id, _MASKED_PURPOSE, row, masked.masked.encode()
        )
        detail_sealed = await self._seal(
            uow,
            document.project_id,
            _DETAIL_PURPOSE,
            row,
            json.dumps(detail.to_json(), separators=(",", ":")).encode(),
        )
        return masked_sealed, detail_sealed

    async def open_masked(self, uow: UnitOfWork, mask: DocumentMask) -> str:
        row = f"{mask.document_id}:v{mask.version}"
        opened = await self._open(
            uow, mask.project_id, _MASKED_PURPOSE, row, mask.masked_sealed
        )
        return opened.decode()

    async def open_detail(self, uow: UnitOfWork, mask: DocumentMask) -> MaskDetail:
        row = f"{mask.document_id}:v{mask.version}"
        opened = await self._open(
            uow, mask.project_id, _DETAIL_PURPOSE, row, mask.detail_sealed
        )
        return MaskDetail.from_json(json.loads(opened))


@dataclass(frozen=True, slots=True)
class DocumentView:
    """A document as a member sees it in the list: its record, its name, its last decision."""

    document: Document
    name: str
    last_review: DocumentReview | None


@dataclass(frozen=True, slots=True)
class DocumentListing:
    documents: tuple[DocumentView, ...]
    usage: DocumentUsage
    policy: UploadPolicy


@dataclass(frozen=True, slots=True)
class MaskedDocumentView:
    """What the model will see: masked text, and what each placeholder hides and why."""

    document: Document
    masked_text: str
    regions: tuple[MaskedRegion, ...]
    masked_page_starts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReviewView:
    """The owner's review screen: the raw text, its spans, and the masks removed."""

    document: Document
    raw_text: str
    masked_text: str
    spans: tuple[MaskSpan, ...]
    exemptions: tuple[MaskExemption, ...]
    raw_page_starts: tuple[int, ...]
    regions: tuple[MaskedRegion, ...]


# Scanned and read: what a document needs before use when there is no masking to review.
_READY = frozenset({DocumentStatus.AWAITING_REVIEW, DocumentStatus.APPROVED})


def _usage(documents: Sequence[Document]) -> DocumentUsage:
    return DocumentUsage(len(documents), sum(d.size_bytes for d in documents))


def _with_rules(
    raw: str, spans: Sequence[MaskSpan], rules: Sequence[MaskingRule]
) -> tuple[MaskSpan, ...]:
    # A rule added since the last masking reaches the text now; a rule removed
    # since keeps its spans, so a correction never quietly masks less.
    return tuple(sorted({*spans, *rule_spans(raw, rules)}))


def _detectors(masked_with: Sequence[str]) -> tuple[str, ...]:
    return tuple(source for source in masked_with if not source.startswith("rule:"))


class DocumentService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        files: FileStore,
        limiter: RateLimiter,
        signals: Signals,
        rules: MaskingRuleService,
        policy: UploadPolicy,
        runs: RunService | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._seal = DocumentSeal(encryption)
        self._audit = audit
        self._files = files
        self._limiter = limiter
        self._signals = signals
        self._rules = rules
        self._policy = policy
        self._runs = runs
        self._clock = clock

    @property
    def policy(self) -> UploadPolicy:
        return self._policy

    async def admit_upload(self, current: Current, project_id: UUID) -> None:
        """Refuse an upload before its body is read: switched off, or too many lately.

        Counted before the slow part, so parallel requests cannot all slip through a
        check-then-count gap.
        """
        if not self._policy.is_enabled:
            raise DocumentRefusedError(
                UploadRefusal.UPLOADS_DISABLED, self._policy.disabled_reason or ""
            )
        decision = await self._limiter.hit(
            f"documents:upload:{current.user.id}", self._policy.uploads_per_hour, _UPLOAD_WINDOW
        )
        if not decision.allowed:
            raise ThrottledError(decision.retry_after)

    async def upload(
        self,
        current: Current,
        project_id: UUID,
        filename: str,
        content: bytes,
        request_id: str | None = None,
    ) -> DocumentView:
        """Store the file sealed and queue it for scanning, reading and masking.

        Raise `DocumentRefusedError` for an empty, unrecognised, oversized or
        over-quota file. Nothing is stored for a refused one.
        """
        self._policy.check_size(len(content))
        file_type = detect_document_type(content, filename)
        name = safe_filename(filename)
        document_id = uuid.uuid4()
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.MANAGE_DOCUMENTS, not_found=_NO_PROJECT
            )
            self._policy.check_quota(
                _usage(await uow.documents.list_for_project(project_id)), len(content)
            )
            sealed = await self._seal.seal_file(uow, project_id, document_id, content)
            name_sealed = await self._seal.seal_name(uow, project_id, document_id, name)
        key = file_key(project_id, document_id)
        await self._files.put(key, sealed)
        try:
            async with self._storage.unit_of_work() as uow:
                await require_member(
                    uow,
                    current.user.id,
                    project_id,
                    Action.MANAGE_DOCUMENTS,
                    not_found=_NO_PROJECT,
                )
                # The project's lock serialises uploads, so the quota and the
                # next tag number hold under concurrent requests (ADR-0002).
                await uow.projects.lock(project_id)
                self._policy.check_quota(
                    _usage(await uow.documents.list_for_project(project_id)), len(content)
                )
                document = Document(
                    id=document_id,
                    project_id=project_id,
                    number=await uow.documents.next_number(project_id),
                    name_sealed=name_sealed,
                    file_type=file_type,
                    size_bytes=len(content),
                    status=DocumentStatus.UPLOADED,
                    uploaded_by=current.user.id,
                    uploaded_at=self._clock(),
                )
                await uow.documents.add(document)
                await self._record(uow, current, request_id, "document.uploaded", document)
                await uow.commit()
        except BaseException:
            await self._files.delete(key)
            raise
        await self._signals.publish(DOCUMENTS_CHANNEL)
        return DocumentView(document, name, None)

    async def list(self, current: Current, project_id: UUID) -> DocumentListing:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            documents = await uow.documents.list_for_project(project_id)
            views = tuple([await self._view(uow, d) for d in documents])
        return DocumentListing(views, _usage(documents), self._policy)

    async def get(self, current: Current, project_id: UUID, document_id: UUID) -> DocumentView:
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.READ_PROJECT
            )
            return await self._view(uow, document)

    async def masked(
        self, current: Current, project_id: UUID, document_id: UUID
    ) -> MaskedDocumentView:
        """The masked text any member may read. 404 until the document has been masked."""
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.READ_PROJECT
            )
            mask = await uow.documents.get_mask(document.id)
            if mask is None:
                raise NotFoundError("This document has not been masked yet.")
            masked_text = await self._seal.open_masked(uow, mask)
            detail = await self._seal.open_detail(uow, mask)
        regions = masked_regions(MaskedText(masked_text, detail.spans, {}))
        return MaskedDocumentView(document, masked_text, regions, detail.masked_page_starts)

    async def review(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        request_id: str | None = None,
    ) -> ReviewView:
        """The raw text and its masks, for the owner only. Every opening is audited."""
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.VIEW_RAW_TEXT
            )
            view = await self._review_view(uow, document)
            await self._record(uow, current, request_id, "document.raw_text_viewed", document)
            await uow.commit()
        return view

    async def add_mask(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        *,
        version: int,
        start: int,
        end: int,
        entity_type: EntityType,
        request_id: str | None = None,
    ) -> ReviewView:
        """Mask `raw[start:end]` as `entity_type`. Raise `ValueError` for an unusable range."""

        def change(
            raw: str, detail: MaskDetail
        ) -> tuple[tuple[MaskSpan, ...], tuple[MaskExemption, ...]]:
            span = user_span(raw, start, end, entity_type, current.user.id)
            return (*detail.spans, span), detail.exemptions

        return await self._correct(
            current, project_id, document_id, version, change, "document.mask_added", request_id
        )

    async def remove_mask(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        *,
        version: int,
        start: int,
        end: int,
        reason: str,
        request_id: str | None = None,
    ) -> ReviewView:
        """Mark the masks exactly over `[start, end)` as not personal, with a reason.

        Raise `LookupError` if no mask covers exactly that range, and
        `ValueError` for a reason that is unusable or a mask that may not go.
        """
        why = normalize_reason(reason)

        def change(
            raw: str, detail: MaskDetail
        ) -> tuple[tuple[MaskSpan, ...], tuple[MaskExemption, ...]]:
            removed = removable_spans(detail.spans, start, end)
            kept = tuple(s for s in detail.spans if s not in removed)
            exempted = tuple(
                MaskExemption(s.start, s.end, s.entity_type, s.source, current.user.id, why)
                for s in removed
            )
            return kept, (*detail.exemptions, *exempted)

        return await self._correct(
            current,
            project_id,
            document_id,
            version,
            change,
            "document.mask_removed",
            request_id,
        )

    async def decide(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        decision: DocumentDecision,
        *,
        version: int,
        request_id: str | None = None,
    ) -> DocumentView:
        """Approve or reject the masked version the reviewer saw. Raise `DocumentStateError`
        if the document is not waiting for review or its masking changed since."""
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.MANAGE_DOCUMENTS
            )
            saved = await uow.documents.save(document.decided(decision, version))
            review = DocumentReview(
                id=uuid.uuid4(),
                document_id=document.id,
                project_id=project_id,
                decision=decision,
                mask_version=version,
                decided_by=current.user.id,
                decided_at=self._clock(),
            )
            await uow.documents.add_review(review)
            action = (
                "document.approved"
                if decision is DocumentDecision.APPROVE
                else "document.rejected"
            )
            await self._record(
                uow, current, request_id, action, document, details={"mask_version": version}
            )
            await uow.commit()
            name = await self._seal.name(uow, saved)
        await self._resume_runs(current, project_id, request_id)
        return DocumentView(saved, name, review)

    async def delete(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        request_id: str | None = None,
    ) -> None:
        """Delete the document, its raw and masked text, then its file. Audited by id."""
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.MANAGE_DOCUMENTS
            )
            if not await uow.documents.delete(document.id):
                raise NotFoundError(_NO_DOCUMENT)
            await self._record(uow, current, request_id, "document.deleted", document)
            await uow.commit()
        try:
            await self._files.delete(file_key(project_id, document_id))
        except OSError:
            # The rows are gone and the file is sealed under the project's key;
            # the operator is told so the leftover can be removed.
            _log.warning("document_file_not_deleted", document_id=str(document_id))
        await self._resume_runs(current, project_id, request_id)

    async def _correct(
        self,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        version: int,
        change: Callable[
            [str, MaskDetail], tuple[tuple[MaskSpan, ...], tuple[MaskExemption, ...]]
        ],
        action: str,
        request_id: str | None,
    ) -> ReviewView:
        async with self._storage.unit_of_work() as uow:
            document = await self._document(
                uow, current, project_id, document_id, Action.VIEW_RAW_TEXT
            )
            document.corrected({})
            if document.mask_version != version:
                raise DocumentStateError(_MASKING_CHANGED)
            text, mask = await self._text_and_mask(uow, document)
            raw = await self._seal.open_raw(uow, text)
            detail = await self._seal.open_detail(uow, mask)
            rules = await self._rules.rules_for(uow, project_id)
        spans, exemptions = change(raw, detail)
        spans = _with_rules(raw, spans, rules)
        masked, page_starts = remask(raw, spans, text.page_starts)
        new_detail = MaskDetail(masked.spans, exemptions, page_starts)
        async with self._storage.unit_of_work() as uow:
            fresh = await self._document(
                uow, current, project_id, document_id, Action.VIEW_RAW_TEXT
            )
            if fresh.version != document.version:
                raise DocumentStateError(_MASKING_CHANGED)
            saved = await uow.documents.save(fresh.corrected(masked.counts))
            masked_sealed, detail_sealed = await self._seal.seal_mask(
                uow, saved, saved.mask_version, masked, new_detail
            )
            stored = DocumentMask(
                document_id=saved.id,
                project_id=project_id,
                version=saved.mask_version,
                masked_sealed=masked_sealed,
                detail_sealed=detail_sealed,
                masked_with=masking_fingerprint(_detectors(mask.masked_with), rules),
                masked_at=self._clock(),
                masked_by=current.user.id,
            )
            if not await uow.documents.put_mask(stored, replaces=version):
                raise DocumentStateError(_MASKING_CHANGED)
            await self._record(
                uow,
                current,
                request_id,
                action,
                saved,
                details={"mask_version": saved.mask_version},
            )
            await uow.commit()
        return ReviewView(
            document=saved,
            raw_text=raw,
            masked_text=masked.masked,
            spans=masked.spans,
            exemptions=exemptions,
            raw_page_starts=text.page_starts,
            regions=masked_regions(masked),
        )

    async def _review_view(self, uow: UnitOfWork, document: Document) -> ReviewView:
        text, mask = await self._text_and_mask(uow, document)
        raw = await self._seal.open_raw(uow, text)
        masked_text = await self._seal.open_masked(uow, mask)
        detail = await self._seal.open_detail(uow, mask)
        return ReviewView(
            document=document,
            raw_text=raw,
            masked_text=masked_text,
            spans=detail.spans,
            exemptions=detail.exemptions,
            raw_page_starts=text.page_starts,
            regions=masked_regions(MaskedText(masked_text, detail.spans, {})),
        )

    @staticmethod
    async def _text_and_mask(
        uow: UnitOfWork, document: Document
    ) -> tuple[DocumentText, DocumentMask]:
        text = await uow.documents.get_text(document.id)
        mask = await uow.documents.get_mask(document.id)
        if text is None or mask is None:
            raise NotFoundError("This document has not been masked yet.")
        return text, mask

    async def _document(
        self,
        uow: UnitOfWork,
        current: Current,
        project_id: UUID,
        document_id: UUID,
        action: Action,
    ) -> Document:
        await require_member(uow, current.user.id, project_id, action, not_found=_NO_PROJECT)
        document = await uow.documents.get(document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(_NO_DOCUMENT)
        return document

    async def _view(self, uow: UnitOfWork, document: Document) -> DocumentView:
        reviews = await uow.documents.list_reviews(document.id)
        return DocumentView(
            document, await self._seal.name(uow, document), reviews[-1] if reviews else None
        )

    async def _resume_runs(
        self, current: Current, project_id: UUID, request_id: str | None
    ) -> None:
        """Approve the open privacy-review gates once no document waits for review.

        Uses the gate route's conditional write, so a person deciding the gate at the
        same moment wins or loses cleanly.
        """
        if self._runs is None:
            return
        async with self._storage.unit_of_work() as uow:
            documents = await uow.documents.list_for_project(project_id)
            if any(d.status is DocumentStatus.AWAITING_REVIEW for d in documents):
                return
            gate_ids: list[UUID] = []
            for review in await uow.reviews.list_for_project(project_id):
                for run in await uow.runs.list_for_review(review.id):
                    if run.state is not RunState.WAITING:
                        continue
                    gate = await uow.gates.find(run.id, MASK_STAGE, GateKind.PRIVACY_REVIEW)
                    if gate is not None and not gate.is_resolved:
                        gate_ids.append(gate.id)
        for gate_id in gate_ids:
            try:
                await self._runs.resolve_gate(
                    current, gate_id, GateDecision.APPROVE, None, request_id
                )
            except RunConflictError:
                _log.info("privacy_review_gate_already_decided", gate_id=str(gate_id))

    async def _record(
        self,
        uow: UnitOfWork,
        current: Current,
        request_id: str | None,
        action: str,
        document: Document,
        details: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        await self._audit.record(
            uow,
            current.actor(request_id),
            action,
            "document",
            str(document.id),
            scope_id=document.project_id,
            details=details,
        )


class _LostClaimError(Exception):
    """This worker no longer holds the document: deleted, or its lease passed to another."""


class _PostponedError(Exception):
    """The scanner could not be asked; the document went back to the queue unread."""


class DocumentProcessor:
    """The worker's side: scan, extract and mask each uploaded document, one at a time."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        files: FileStore,
        scanner: MalwareScanner,
        extractor: DocumentExtractor,
        sanitizer: Sanitizer,
        detector_sources: Sequence[str],
        rules: MaskingRuleService,
        signals: Signals,
        limits: ExtractionLimits,
        worker_id: str,
        clock: Callable[[], datetime] = _utc_now,
        lease: timedelta = timedelta(minutes=5),
        poll_interval: timedelta = timedelta(seconds=5),
    ) -> None:
        self._storage = storage
        self._seal = DocumentSeal(encryption)
        self._audit = audit
        self._files = files
        self._scanner = scanner
        self._extractor = extractor
        self._sanitizer = sanitizer
        self._detector_sources = tuple(sorted(detector_sources))
        self._rules = rules
        self._signals = signals
        self._limits = limits
        self._worker_id = worker_id
        self._clock = clock
        self._lease = lease
        self._poll_interval = poll_interval

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def serve(
        self, stop: asyncio.Event, *, on_alive: Callable[[], None] = _nothing
    ) -> None:
        """Process documents until `stop` is set. One in progress is left to its lease."""
        while not stop.is_set():
            on_alive()
            work = asyncio.create_task(self.run_once())
            stopping = asyncio.create_task(stop.wait())
            await asyncio.wait({work, stopping}, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            if not work.done():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await work
                return
            if not self._worked(work):
                await self._idle(stop)

    async def run_once(self) -> bool:
        """Recover expired leases, then process one uploaded document. True if there was any."""
        recovered = await self.recover_expired()
        async with self._storage.unit_of_work() as uow:
            document = await uow.documents.claim_next(
                self._worker_id, self._clock(), self._lease
            )
            await uow.commit()
        if document is None:
            return recovered > 0
        heartbeat = asyncio.create_task(self._heartbeat(document.id))
        try:
            await self._process(document)
        except _LostClaimError:
            _log.info("document_claim_lost", document_id=str(document.id))
        except _PostponedError:
            # Nothing was done: report no work, so `serve` waits a poll interval
            # instead of claiming the same document again at once.
            return False
        finally:
            heartbeat.cancel()
        return True

    async def recover_expired(self) -> int:
        """Queue again, or fail after too many attempts, documents whose worker stopped."""
        async with self._storage.unit_of_work() as uow:
            expired = await uow.documents.lock_expired(self._clock(), _RECOVERY_BATCH)
            for document in expired:
                if document.attempts >= MAX_PROCESSING_ATTEMPTS:
                    await uow.documents.save(document.failed(DocumentFailure.INTERRUPTED))
                else:
                    await uow.documents.save(document.requeued())
            await uow.commit()
        if expired:
            await self._signals.publish(DOCUMENTS_CHANNEL)
        return len(expired)

    async def _process(self, document: Document) -> None:
        sealed = await self._files.get(file_key(document.project_id, document.id))
        if sealed is None:
            await self._fail(document.id, DocumentFailure.UNREADABLE)
            return
        try:
            async with self._storage.unit_of_work() as uow:
                content = await self._seal.open_file(uow, document, sealed)
        except KeyDestroyedError:
            # The project was deleted: its files are already unreadable.
            raise _LostClaimError from None
        if not await self._scan(document, content):
            return
        try:
            extracted = await self._extractor.extract(content, document.file_type, self._limits)
        except ExtractionFailedError as exc:
            await self._fail(document.id, exc.failure)
            return
        await self._step(document.id, lambda d: d.extracted(extracted, self._until()))
        async with self._storage.unit_of_work() as uow:
            rules = list(await self._rules.rules_for(uow, document.project_id))
        masked = await self._sanitizer.mask(extracted.text, rules=rules)
        _, page_starts = remask(extracted.text, masked.spans, extracted.page_starts)
        await self._store(
            document, extracted.text, extracted.page_starts, masked, page_starts, rules
        )

    async def _scan(self, document: Document, content: bytes) -> bool:
        try:
            verdict = await self._scanner.scan(content)
        except MalwareScanUnavailableError:
            _log.warning("malware_scanner_unavailable", document_id=str(document.id))
            await self._step(document.id, lambda d: d.requeued())
            raise _PostponedError from None
        except MalwareScanRefusedError:
            await self._fail(document.id, DocumentFailure.SCAN_FAILED)
            return False
        if verdict.outcome is ScanOutcome.INFECTED:
            await self._files.delete(file_key(document.project_id, document.id))
            await self._fail(
                document.id,
                DocumentFailure.MALWARE_FOUND,
                details={"scanner": verdict.scanner, "signature": verdict.signature},
            )
            return False
        await self._step(document.id, lambda d: d.scanned(verdict.scanner, self._until()))
        return True

    async def _store(
        self,
        document: Document,
        raw: str,
        raw_page_starts: tuple[int, ...],
        masked: MaskedText,
        masked_page_starts: tuple[int, ...],
        rules: Sequence[MaskingRule],
    ) -> None:
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            current = await self._held(uow, document.id)
            saved = await uow.documents.save(current.masked(masked.counts))
            await uow.documents.add_text(
                DocumentText(
                    document_id=saved.id,
                    project_id=saved.project_id,
                    raw_sealed=await self._seal.seal_raw(uow, saved, raw),
                    page_starts=raw_page_starts,
                    extracted_at=now,
                )
            )
            detail = MaskDetail(masked.spans, (), masked_page_starts)
            masked_sealed, detail_sealed = await self._seal.seal_mask(
                uow, saved, 1, masked, detail
            )
            stored = await uow.documents.put_mask(
                DocumentMask(
                    document_id=saved.id,
                    project_id=saved.project_id,
                    version=1,
                    masked_sealed=masked_sealed,
                    detail_sealed=detail_sealed,
                    masked_with=masking_fingerprint(self._detector_sources, rules),
                    masked_at=now,
                ),
                replaces=None,
            )
            if not stored:
                raise _LostClaimError
            await self._system_record(uow, saved, "document.ready_for_review")
            await uow.commit()

    async def _step(self, document_id: UUID, change: Callable[[Document], Document]) -> None:
        async with self._storage.unit_of_work() as uow:
            current = await self._held(uow, document_id)
            try:
                await uow.documents.save(change(current))
            except ConcurrentUpdateError as exc:
                raise _LostClaimError from exc
            await uow.commit()

    async def _fail(
        self,
        document_id: UUID,
        failure: DocumentFailure,
        details: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        async with self._storage.unit_of_work() as uow:
            current = await self._held(uow, document_id)
            try:
                failed = await uow.documents.save(current.failed(failure))
            except ConcurrentUpdateError as exc:
                raise _LostClaimError from exc
            await self._system_record(
                uow, failed, "document.failed", {"failure": failure.value, **(details or {})}
            )
            await uow.commit()

    async def _held(self, uow: UnitOfWork, document_id: UUID) -> Document:
        current = await uow.documents.get(document_id)
        if current is None or current.claimed_by != self._worker_id:
            raise _LostClaimError
        return current

    async def _system_record(
        self,
        uow: UnitOfWork,
        document: Document,
        action: str,
        details: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        await self._audit.record(
            uow,
            SYSTEM,
            action,
            "document",
            str(document.id),
            scope_id=document.project_id,
            details=details,
        )

    def _until(self) -> datetime:
        return self._clock() + self._lease

    async def _heartbeat(self, document_id: UUID) -> None:
        interval = self._lease.total_seconds() / 3
        while True:
            await asyncio.sleep(interval)
            async with self._storage.unit_of_work() as uow:
                renewed = await uow.documents.renew_lease(
                    document_id, self._worker_id, self._until()
                )
                await uow.commit()
            if not renewed:
                return

    @staticmethod
    def _worked(work: asyncio.Task[bool]) -> bool:
        try:
            return work.result()
        except StorageUnavailableError:
            _log.warning("document_worker_storage_unavailable")
        except Exception:
            _log.exception("document_worker_turn_failed")
        return False

    async def _idle(self, stop: asyncio.Event) -> None:
        signalled = asyncio.create_task(
            self._signals.wait(DOCUMENTS_CHANNEL, self._poll_interval.total_seconds())
        )
        stopping = asyncio.create_task(stop.wait())
        await asyncio.wait({signalled, stopping}, return_when=asyncio.FIRST_COMPLETED)
        for task in (signalled, stopping):
            task.cancel()


class DocumentReviewQueue:
    """`DocumentsAwaitingReview` for the mask stage: what the privacy_review gate lists."""

    def __init__(self, storage: StorageProvider) -> None:
        self._storage = storage

    async def awaiting_review(self, project_id: UUID) -> tuple[DocumentAwaitingReview, ...]:
        async with self._storage.unit_of_work() as uow:
            documents = await uow.documents.list_for_project(project_id)
        return tuple(
            DocumentAwaitingReview(
                d.id, {EntityType(kind): n for kind, n in d.masked_counts.items()}
            )
            for d in documents
            if d.status is DocumentStatus.AWAITING_REVIEW
        )


class DocumentEvidence:
    """`DocumentPassageSource`: the masked passages of the project's approved documents.

    A document counts only while it is approved and its current masking is the
    version its last approval names; anything else, a model never sees. With the
    project's masking off, there is nothing to review: every scanned and read document
    counts, as written apart from the project's rule terms.
    """

    def __init__(self, encryption: ScopeEncryption, rules: MaskingRuleService) -> None:
        self._seal = DocumentSeal(encryption)
        self._rules = rules

    async def approved_passages(self, uow: UnitOfWork, project_id: UUID) -> tuple[Passage, ...]:
        privacy = await self._rules.privacy_for(uow, project_id)
        if not privacy.is_masked:
            return await self._unmasked_passages(uow, project_id, privacy.rules)
        passages: list[Passage] = []
        for document in await uow.documents.list_for_project(project_id):
            if document.status is not DocumentStatus.APPROVED:
                continue
            mask = await uow.documents.get_mask(document.id)
            reviews = await uow.documents.list_reviews(document.id)
            last = reviews[-1] if reviews else None
            if (
                mask is None
                or last is None
                or last.decision is not DocumentDecision.APPROVE
                or last.mask_version != mask.version
                or mask.version != document.mask_version
            ):
                continue
            text = await self._seal.open_masked(uow, mask)
            detail = await self._seal.open_detail(uow, mask)
            passages.extend(document_passages(document, text, detail.masked_page_starts))
        return tuple(passages)

    async def _unmasked_passages(
        self, uow: UnitOfWork, project_id: UUID, rules: Sequence[MaskingRule]
    ) -> tuple[Passage, ...]:
        passages: list[Passage] = []
        for document in await uow.documents.list_for_project(project_id):
            if document.status not in _READY:
                continue
            text = await uow.documents.get_text(document.id)
            if text is None:
                continue
            raw = await self._seal.open_raw(uow, text)
            sent, page_starts = remask(raw, rule_spans(raw, rules), text.page_starts)
            passages.extend(document_passages(document, sent.masked, page_starts))
        return tuple(passages)
