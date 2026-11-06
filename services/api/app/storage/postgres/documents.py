"""PostgreSQL tables and repository for uploaded documents (migration 0013).

The tables live here rather than in `tables.py`, on the same `metadata`, so the
documents feature is one module. Every text column is sealed under the
project's key; what stays in the clear is what a worker or a list needs
without opening anything: status, type, size, counts of masked items by type.

Writes that race are conditional: a claim skips rows another worker holds, a
save checks `version`, a masking replaces only the version it read, and the
next tag number moves only forward, so a deleted document's tag is never
given to another (architecture §5.7).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Row,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.documents import (
    PROCESSING_STATUSES,
    Document,
    DocumentDecision,
    DocumentFailure,
    DocumentMask,
    DocumentReview,
    DocumentStatus,
    DocumentText,
    DocumentType,
)
from app.domain.errors import ConcurrentUpdateError
from app.storage.postgres.tables import metadata

documents = Table(
    "documents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("name_sealed", LargeBinary, nullable=False),
    Column("file_type", String(16), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("failure", String(32), nullable=True),
    Column("scanned_with", String(128), nullable=True),
    Column("extracted_with", String(128), nullable=True),
    Column("page_count", Integer, nullable=True),
    Column("character_count", Integer, nullable=True),
    Column("is_truncated", Boolean, nullable=False),
    Column("masked_counts", JSONB, nullable=False),
    Column("mask_version", Integer, nullable=False),
    Column("uploaded_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(128), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("attempts", SmallInteger, nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("project_id", "number", name="uq_documents_project_number"),
    CheckConstraint(
        "status IN ('uploaded', 'scanning', 'extracting', 'masking', 'awaiting_review', "
        "'approved', 'rejected', 'failed')",
        name="ck_documents_status",
    ),
    Index("ix_documents_queue", "uploaded_at", postgresql_where=text("status = 'uploaded'")),
    Index(
        "ix_documents_leases",
        "lease_until",
        postgresql_where=text("status IN ('scanning', 'extracting', 'masking')"),
    ),
)

document_numbers = Table(
    "document_numbers",
    metadata,
    Column("project_id", Uuid, ForeignKey("projects.id"), primary_key=True),
    Column("highest_number", Integer, nullable=False),
)

document_texts = Table(
    "document_texts",
    metadata,
    Column(
        "document_id", Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("raw_sealed", LargeBinary, nullable=False),
    Column("page_starts", ARRAY(Integer), nullable=False),
    Column("extracted_at", DateTime(timezone=True), nullable=False),
)

document_masks = Table(
    "document_masks",
    metadata,
    Column(
        "document_id", Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("masked_sealed", LargeBinary, nullable=False),
    Column("detail_sealed", LargeBinary, nullable=False),
    Column("masked_with", ARRAY(String(128)), nullable=False),
    Column("masked_at", DateTime(timezone=True), nullable=False),
    Column("masked_by", Uuid, ForeignKey("users.id"), nullable=True),
)

document_reviews = Table(
    "document_reviews",
    metadata,
    Column("id", Uuid, primary_key=True),
    # No foreign key to documents: a decision is provenance and outlives the file.
    Column("document_id", Uuid, nullable=False, index=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("mask_version", Integer, nullable=False),
    Column("decided_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
)


def _to_document(row: Row[Any]) -> Document:
    return Document(
        id=row.id,
        project_id=row.project_id,
        number=row.number,
        name_sealed=row.name_sealed,
        file_type=DocumentType(row.file_type),
        size_bytes=row.size_bytes,
        status=DocumentStatus(row.status),
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
        failure=None if row.failure is None else DocumentFailure(row.failure),
        scanned_with=row.scanned_with,
        extracted_with=row.extracted_with,
        page_count=row.page_count,
        character_count=row.character_count,
        is_truncated=row.is_truncated,
        masked_counts=dict(row.masked_counts),
        mask_version=row.mask_version,
        claimed_by=row.claimed_by,
        lease_until=row.lease_until,
        attempts=row.attempts,
        version=row.version,
    )


def _values(document: Document) -> dict[str, Any]:
    return {
        "project_id": document.project_id,
        "number": document.number,
        "name_sealed": document.name_sealed,
        "file_type": document.file_type.value,
        "size_bytes": document.size_bytes,
        "status": document.status.value,
        "failure": None if document.failure is None else document.failure.value,
        "scanned_with": document.scanned_with,
        "extracted_with": document.extracted_with,
        "page_count": document.page_count,
        "character_count": document.character_count,
        "is_truncated": document.is_truncated,
        "masked_counts": dict(document.masked_counts),
        "mask_version": document.mask_version,
        "uploaded_by": document.uploaded_by,
        "uploaded_at": document.uploaded_at,
        "claimed_by": document.claimed_by,
        "lease_until": document.lease_until,
        "attempts": document.attempts,
    }


def _to_mask(row: Row[Any]) -> DocumentMask:
    return DocumentMask(
        document_id=row.document_id,
        project_id=row.project_id,
        version=row.version,
        masked_sealed=row.masked_sealed,
        detail_sealed=row.detail_sealed,
        masked_with=tuple(row.masked_with),
        masked_at=row.masked_at,
        masked_by=row.masked_by,
    )


_PROCESSING = [s.value for s in PROCESSING_STATUSES]


class PostgresDocumentRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def _one(self, query: Any) -> Document | None:
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else _to_document(row)

    async def get(self, document_id: UUID) -> Document | None:
        return await self._one(select(documents).where(documents.c.id == document_id))

    async def list_for_project(self, project_id: UUID) -> list[Document]:
        query = (
            select(documents)
            .where(documents.c.project_id == project_id)
            .order_by(documents.c.number)
        )
        return [_to_document(row) for row in await self._connection.execute(query)]

    async def next_number(self, project_id: UUID) -> int:
        query = select(document_numbers.c.highest_number).where(
            document_numbers.c.project_id == project_id
        )
        highest = (await self._connection.execute(query)).scalar_one_or_none()
        return (highest or 0) + 1

    async def add(self, document: Document, /) -> None:
        moved = await self._connection.execute(
            pg_insert(document_numbers)
            .values(project_id=document.project_id, highest_number=document.number)
            .on_conflict_do_update(
                index_elements=[document_numbers.c.project_id],
                set_={"highest_number": document.number},
                where=document_numbers.c.highest_number < document.number,
            )
        )
        if moved.rowcount != 1:
            raise ConcurrentUpdateError(f"document number {document.number} is taken")
        await self._connection.execute(
            insert(documents).values(
                id=document.id, version=document.version, **_values(document)
            )
        )

    async def save(self, document: Document, /) -> Document:
        result = await self._connection.execute(
            update(documents)
            .where(documents.c.id == document.id, documents.c.version == document.version)
            .values(version=document.version + 1, **_values(document))
            .returning(*documents.c)
        )
        row = result.one_or_none()
        if row is None:
            raise ConcurrentUpdateError(f"document {document.id} changed or is gone")
        return _to_document(row)

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> Document | None:
        # SKIP LOCKED: a document another worker is claiming is passed over, not
        # waited for. The partial index ix_documents_queue serves this.
        oldest = await self._one(
            select(documents)
            .where(documents.c.status == DocumentStatus.UPLOADED.value)
            .order_by(documents.c.uploaded_at, documents.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if oldest is None:
            return None
        return await self.save(oldest.claimed(worker_id, now + lease))

    async def renew_lease(self, document_id: UUID, worker_id: str, until: datetime) -> bool:
        result = await self._connection.execute(
            update(documents)
            .where(
                documents.c.id == document_id,
                documents.c.status.in_(_PROCESSING),
                documents.c.claimed_by == worker_id,
            )
            .values(lease_until=until)
        )
        return result.rowcount == 1

    async def lock_expired(self, now: datetime, limit: int) -> list[Document]:
        result = await self._connection.execute(
            select(documents)
            .where(documents.c.status.in_(_PROCESSING), documents.c.lease_until < now)
            .order_by(documents.c.lease_until, documents.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_to_document(row) for row in result]

    async def delete(self, document_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(documents).where(documents.c.id == document_id)
        )
        return result.rowcount > 0

    async def add_text(self, text: DocumentText, /) -> bool:
        result = await self._connection.execute(
            pg_insert(document_texts)
            .values(
                document_id=text.document_id,
                project_id=text.project_id,
                raw_sealed=text.raw_sealed,
                page_starts=list(text.page_starts),
                extracted_at=text.extracted_at,
            )
            .on_conflict_do_nothing(index_elements=[document_texts.c.document_id])
        )
        return result.rowcount > 0

    async def get_text(self, document_id: UUID) -> DocumentText | None:
        query = select(document_texts).where(document_texts.c.document_id == document_id)
        row = (await self._connection.execute(query)).one_or_none()
        if row is None:
            return None
        return DocumentText(
            document_id=row.document_id,
            project_id=row.project_id,
            raw_sealed=row.raw_sealed,
            page_starts=tuple(row.page_starts),
            extracted_at=row.extracted_at,
        )

    async def put_mask(self, mask: DocumentMask, /, *, replaces: int | None) -> bool:
        values: dict[str, Any] = {
            "project_id": mask.project_id,
            "version": mask.version,
            "masked_sealed": mask.masked_sealed,
            "detail_sealed": mask.detail_sealed,
            "masked_with": list(mask.masked_with),
            "masked_at": mask.masked_at,
            "masked_by": mask.masked_by,
        }
        if replaces is None:
            statement: Any = (
                pg_insert(document_masks)
                .values(document_id=mask.document_id, **values)
                .on_conflict_do_nothing(index_elements=[document_masks.c.document_id])
            )
        else:
            statement = (
                update(document_masks)
                .where(
                    document_masks.c.document_id == mask.document_id,
                    document_masks.c.version == replaces,
                )
                .values(**values)
            )
        result = await self._connection.execute(statement)
        return result.rowcount > 0

    async def get_mask(self, document_id: UUID) -> DocumentMask | None:
        query = select(document_masks).where(document_masks.c.document_id == document_id)
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else _to_mask(row)

    async def add_review(self, review: DocumentReview, /) -> None:
        await self._connection.execute(
            insert(document_reviews).values(
                id=review.id,
                document_id=review.document_id,
                project_id=review.project_id,
                decision=review.decision.value,
                mask_version=review.mask_version,
                decided_by=review.decided_by,
                decided_at=review.decided_at,
            )
        )

    async def list_reviews(self, document_id: UUID) -> list[DocumentReview]:
        query = (
            select(document_reviews)
            .where(document_reviews.c.document_id == document_id)
            .order_by(document_reviews.c.decided_at, document_reviews.c.id)
        )
        return [
            DocumentReview(
                id=row.id,
                document_id=row.document_id,
                project_id=row.project_id,
                decision=DocumentDecision(row.decision),
                mask_version=row.mask_version,
                decided_by=row.decided_by,
                decided_at=row.decided_at,
            )
            for row in await self._connection.execute(query)
        ]
