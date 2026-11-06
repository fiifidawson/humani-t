"""Request and response models for a project's uploaded documents.

Pydantic checks shape and size; the domain checks meaning (`user_span`,
`normalize_reason`, `Document.decided`), and its errors become 404, 409 or 422
in the route. The upload itself is multipart and has no model here
(`api/uploads.py`).

Raw text appears in exactly one response, `DocumentReviewOut`, which only an
owner can get and every request for which is audited. Every other response
carries masked text, counts and ids.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.documents import (
    DocumentListing,
    DocumentView,
    MaskedDocumentView,
    ReviewView,
)
from app.domain.documents import (
    ACCEPTED_TYPES,
    FAILURE_MESSAGES,
    MAX_REASON_LENGTH,
    DocumentDecision,
    DocumentFailure,
    DocumentStatus,
    DocumentType,
    MaskedRegion,
    MaskExemption,
)
from app.domain.masking import STRUCTURED_ENTITY_TYPES, EntityType, MaskSpan

_MAX_OFFSET = 10_000_000


class ReviewDecisionOut(BaseModel):
    decision: DocumentDecision
    mask_version: int = Field(description="The masked version the decision was about.")
    decided_by: UUID
    decided_at: datetime


class DocumentOut(BaseModel):
    id: UUID
    tag: str = Field(description="How a brief cites it: D and its number, e.g. D2.")
    name: str = Field(description="The file's name as uploaded, made safe to show.")
    file_type: DocumentType = Field(description="Recognised from the file's bytes.")
    size_bytes: int
    status: DocumentStatus
    failure: DocumentFailure | None = None
    failure_message: str | None = Field(
        default=None, description="Why processing stopped, in plain language."
    )
    uploaded_by: UUID
    uploaded_at: datetime
    scanned_with: str | None = Field(
        default=None, description="The malware scanner that checked it; `none` if none did."
    )
    extracted_with: str | None = None
    page_count: int | None = Field(default=None, description="Pages, for a PDF.")
    character_count: int | None = None
    is_truncated: bool = Field(
        description="True when only the first pages or characters were read."
    )
    masked_items: dict[str, int] = Field(
        description="Distinct masked values by entity type. Counts only, never values."
    )
    mask_version: int = Field(description="0 until masked; then one more per correction.")
    last_review: ReviewDecisionOut | None = None

    @classmethod
    def from_view(cls, view: DocumentView) -> DocumentOut:
        d = view.document
        review = view.last_review
        return cls(
            id=d.id,
            tag=d.tag,
            name=view.name,
            file_type=d.file_type,
            size_bytes=d.size_bytes,
            status=d.status,
            failure=d.failure,
            failure_message=None if d.failure is None else FAILURE_MESSAGES[d.failure],
            uploaded_by=d.uploaded_by,
            uploaded_at=d.uploaded_at,
            scanned_with=d.scanned_with,
            extracted_with=d.extracted_with,
            page_count=d.page_count,
            character_count=d.character_count,
            is_truncated=d.is_truncated,
            masked_items=dict(d.masked_counts),
            mask_version=d.mask_version,
            last_review=None
            if review is None
            else ReviewDecisionOut(
                decision=review.decision,
                mask_version=review.mask_version,
                decided_by=review.decided_by,
                decided_at=review.decided_at,
            ),
        )


class DocumentUsageOut(BaseModel):
    documents: int
    size_bytes: int
    max_documents: int
    max_size_bytes: int = Field(description="The most the project's documents may take.")
    max_file_bytes: int = Field(description="The largest single file accepted.")


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]
    usage: DocumentUsageOut
    accepted_types: list[DocumentType]
    uploads_enabled: bool
    uploads_disabled_reason: str | None = Field(
        default=None, description="Why uploads are off, written for the people affected."
    )

    @classmethod
    def from_listing(cls, listing: DocumentListing) -> DocumentListOut:
        policy = listing.policy
        return cls(
            documents=[DocumentOut.from_view(v) for v in listing.documents],
            usage=DocumentUsageOut(
                documents=listing.usage.documents,
                size_bytes=listing.usage.size_bytes,
                max_documents=policy.max_documents,
                max_size_bytes=policy.max_project_bytes,
                max_file_bytes=policy.max_file_bytes,
            ),
            accepted_types=list(ACCEPTED_TYPES),
            uploads_enabled=policy.is_enabled,
            uploads_disabled_reason=policy.disabled_reason,
        )


class MaskedItemOut(BaseModel):
    """One placeholder in the masked text: where it is, what it hides, what found it."""

    start: int = Field(description="Offset of the placeholder in `masked_text`.")
    end: int
    placeholder: str = Field(description="As it appears in the text, e.g. [PERSON_1].")
    entity_type: EntityType
    sources: list[str] = Field(
        description="What made it: `detector@version`, `rule:<id>` or `user:<id>`."
    )

    @classmethod
    def from_region(cls, region: MaskedRegion, masked_text: str) -> MaskedItemOut:
        return cls(
            start=region.masked_start,
            end=region.masked_end,
            placeholder=masked_text[region.masked_start : region.masked_end],
            entity_type=region.entity_type,
            sources=list(region.sources),
        )


class MaskedDocumentOut(BaseModel):
    """What the model will see once the document is approved."""

    document_id: UUID
    tag: str
    status: DocumentStatus
    mask_version: int
    masked_text: str
    items: list[MaskedItemOut]
    page_starts: list[int] = Field(description="Where each page starts in `masked_text`.")
    is_truncated: bool

    @classmethod
    def from_view(cls, view: MaskedDocumentView) -> MaskedDocumentOut:
        d = view.document
        return cls(
            document_id=d.id,
            tag=d.tag,
            status=d.status,
            mask_version=d.mask_version,
            masked_text=view.masked_text,
            items=[MaskedItemOut.from_region(r, view.masked_text) for r in view.regions],
            page_starts=list(view.masked_page_starts),
            is_truncated=d.is_truncated,
        )


class SpanOut(BaseModel):
    start: int = Field(description="Offset in `raw_text`.")
    end: int
    entity_type: EntityType
    source: str
    can_remove: bool = Field(
        description="False for project rules and for detected structured identifiers."
    )

    @classmethod
    def from_span(cls, span: MaskSpan) -> SpanOut:
        removable = span.source.startswith("user:") or (
            not span.source.startswith("rule:")
            and span.entity_type not in STRUCTURED_ENTITY_TYPES
        )
        return cls(
            start=span.start,
            end=span.end,
            entity_type=span.entity_type,
            source=span.source,
            can_remove=removable,
        )


class ExemptionOut(BaseModel):
    start: int
    end: int
    entity_type: EntityType
    source: str = Field(description="What had masked it.")
    removed_by: UUID
    reason: str

    @classmethod
    def from_domain(cls, exemption: MaskExemption) -> ExemptionOut:
        return cls(
            start=exemption.start,
            end=exemption.end,
            entity_type=exemption.entity_type,
            source=exemption.source,
            removed_by=exemption.removed_by,
            reason=exemption.reason,
        )


class DocumentReviewOut(BaseModel):
    """The owner's masking review. Raw text: owners only, and each request is audited."""

    document_id: UUID
    tag: str
    status: DocumentStatus
    mask_version: int = Field(description="Send it back with a correction or a decision.")
    raw_text: str = Field(description="The original extracted text. This view is recorded.")
    masked_text: str
    spans: list[SpanOut] = Field(description="Masks in force, over `raw_text`.")
    removed: list[ExemptionOut] = Field(description="Masks people removed, and why.")
    items: list[MaskedItemOut] = Field(description="Placeholders in `masked_text`.")
    page_starts: list[int] = Field(description="Where each page starts in `raw_text`.")
    is_truncated: bool
    notice: str = Field(
        default=(
            "You're seeing original text. This view is recorded. Masking is not anonymity: "
            "text that is only in images was not read."
        )
    )

    @classmethod
    def from_view(cls, view: ReviewView) -> DocumentReviewOut:
        d = view.document
        return cls(
            document_id=d.id,
            tag=d.tag,
            status=d.status,
            mask_version=d.mask_version,
            raw_text=view.raw_text,
            masked_text=view.masked_text,
            spans=[SpanOut.from_span(s) for s in view.spans],
            removed=[ExemptionOut.from_domain(e) for e in view.exemptions],
            items=[MaskedItemOut.from_region(r, view.masked_text) for r in view.regions],
            page_starts=list(view.raw_page_starts),
            is_truncated=d.is_truncated,
        )


class MaskIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"version": 1, "start": 120, "end": 132, "entity_type": "PERSON"}
        },
    )

    version: int = Field(ge=1, description="The `mask_version` you were looking at.")
    start: int = Field(ge=0, le=_MAX_OFFSET, description="Offset in `raw_text`.")
    end: int = Field(ge=1, le=_MAX_OFFSET)
    entity_type: EntityType


class UnmaskIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "version": 1,
                "start": 40,
                "end": 46,
                "reason": "A district's name, not a person",
            }
        },
    )

    version: int = Field(ge=1)
    start: int = Field(ge=0, le=_MAX_OFFSET, description="Exactly a mask's `start`.")
    end: int = Field(ge=1, le=_MAX_OFFSET, description="Exactly a mask's `end`.")
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH * 2)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"version": 1}})

    version: int = Field(ge=1, description="The `mask_version` you reviewed.")
