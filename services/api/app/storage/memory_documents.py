"""In-memory storage for uploaded documents, their texts, maskings and reviews.

Kept beside `memory.py`, like the masking tables: `memory.py` holds one
`DocumentState` and copies it with the rest, so these writes commit and roll
back with everything else in the unit of work.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.documents import (
    PROCESSING_STATUSES,
    Document,
    DocumentMask,
    DocumentReview,
    DocumentStatus,
    DocumentText,
)
from app.domain.errors import ConcurrentUpdateError


class DocumentState:
    def __init__(self) -> None:
        self.documents: dict[UUID, Document] = {}
        self.texts: dict[UUID, DocumentText] = {}
        self.masks: dict[UUID, DocumentMask] = {}
        # Tuples, not lists: `copy` is shallow.
        self.reviews: tuple[DocumentReview, ...] = ()
        self.highest_number: dict[UUID, int] = {}

    def copy(self) -> DocumentState:
        clone = DocumentState()
        clone.documents = dict(self.documents)
        clone.texts = dict(self.texts)
        clone.masks = dict(self.masks)
        clone.reviews = self.reviews
        clone.highest_number = dict(self.highest_number)
        return clone


class MemoryDocumentRepository:
    def __init__(self, state: DocumentState) -> None:
        self._state = state

    async def get(self, document_id: UUID) -> Document | None:
        return self._state.documents.get(document_id)

    async def list_for_project(self, project_id: UUID) -> list[Document]:
        found = [d for d in self._state.documents.values() if d.project_id == project_id]
        return sorted(found, key=lambda d: d.number)

    async def next_number(self, project_id: UUID) -> int:
        return self._state.highest_number.get(project_id, 0) + 1

    async def add(self, document: Document, /) -> None:
        if document.id in self._state.documents:
            raise ValueError(f"document {document.id} already exists")
        taken = any(
            d.project_id == document.project_id and d.number == document.number
            for d in self._state.documents.values()
        )
        if taken or document.number <= self._state.highest_number.get(document.project_id, 0):
            raise ConcurrentUpdateError(f"document number {document.number} is taken")
        self._state.documents[document.id] = document
        self._state.highest_number[document.project_id] = document.number

    async def save(self, document: Document, /) -> Document:
        stored = self._state.documents.get(document.id)
        if stored is None or stored.version != document.version:
            raise ConcurrentUpdateError(f"document {document.id} changed or is gone")
        saved = replace(document, version=document.version + 1)
        self._state.documents[document.id] = saved
        return saved

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> Document | None:
        waiting = [
            d for d in self._state.documents.values() if d.status is DocumentStatus.UPLOADED
        ]
        if not waiting:
            return None
        oldest = min(waiting, key=lambda d: (d.uploaded_at, str(d.id)))
        return await self.save(oldest.claimed(worker_id, now + lease))

    async def renew_lease(self, document_id: UUID, worker_id: str, until: datetime) -> bool:
        stored = self._state.documents.get(document_id)
        if stored is None or stored.claimed_by != worker_id or not stored.is_processing:
            return False
        self._state.documents[document_id] = stored.renewed(until)
        return True

    async def lock_expired(self, now: datetime, limit: int) -> list[Document]:
        expired = [
            d
            for d in self._state.documents.values()
            if d.status in PROCESSING_STATUSES
            and d.lease_until is not None
            and d.lease_until < now
        ]
        return sorted(expired, key=lambda d: (d.lease_until or now, str(d.id)))[:limit]

    async def delete(self, document_id: UUID) -> bool:
        self._state.texts.pop(document_id, None)
        self._state.masks.pop(document_id, None)
        return self._state.documents.pop(document_id, None) is not None

    async def add_text(self, text: DocumentText, /) -> bool:
        if text.document_id not in self._state.documents:
            raise ValueError(f"document {text.document_id} does not exist")
        if text.document_id in self._state.texts:
            return False
        self._state.texts[text.document_id] = text
        return True

    async def get_text(self, document_id: UUID) -> DocumentText | None:
        return self._state.texts.get(document_id)

    async def put_mask(self, mask: DocumentMask, /, *, replaces: int | None) -> bool:
        if mask.document_id not in self._state.documents:
            raise ValueError(f"document {mask.document_id} does not exist")
        stored = self._state.masks.get(mask.document_id)
        if (None if stored is None else stored.version) != replaces:
            return False
        self._state.masks[mask.document_id] = mask
        return True

    async def get_mask(self, document_id: UUID) -> DocumentMask | None:
        return self._state.masks.get(document_id)

    async def add_review(self, review: DocumentReview, /) -> None:
        if any(r.id == review.id for r in self._state.reviews):
            raise ValueError(f"document review {review.id} already exists")
        self._state.reviews = (*self._state.reviews, review)

    async def list_reviews(self, document_id: UUID) -> list[DocumentReview]:
        found = [r for r in self._state.reviews if r.document_id == document_id]
        return sorted(found, key=lambda r: (r.decided_at, str(r.id)))
