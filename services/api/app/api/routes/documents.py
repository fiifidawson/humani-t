"""A project's own documents: upload, status, masked text, the owner's masking review.

Any member lists documents and reads masked text; only owners upload, delete, see
raw text, correct masks and approve. The upload is the only non-JSON body, so it
keeps every CSRF layer except the JSON-only rule.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.access import require
from app.api.dependencies import API_PREFIX, DocumentsDep
from app.api.schemas.documents import (
    DecisionIn,
    DocumentListOut,
    DocumentOut,
    DocumentReviewOut,
    MaskedDocumentOut,
    MaskIn,
    UnmaskIn,
)
from app.api.session import request_id
from app.api.uploads import (
    FILE_FIELD,
    MULTIPART_OVERHEAD_BYTES,
    UploadFormatError,
    UploadTooLargeError,
    parse_single_file,
    read_capped,
)
from app.application.accounts import Current
from app.domain.documents import (
    DocumentDecision,
    DocumentRefusedError,
    DocumentStateError,
    UploadRefusal,
)
from app.domain.errors import RateLimiterUnavailableError
from app.domain.projects import Action

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])

# The one path that accepts a multipart body and a body larger than 1 MiB.
UPLOAD_PATH = re.compile(rf"^{re.escape(API_PREFIX)}/projects/[^/]+/documents/?$")


def upload_body_limit(max_file_bytes: int) -> int:
    """The most an upload's body may hold: the file and its multipart framing."""
    return max_file_bytes + MULTIPART_OVERHEAD_BYTES


_Member = Annotated[Current, require(Action.READ_PROJECT)]
_Owner = Annotated[Current, require(Action.MANAGE_DOCUMENTS)]
_RawTextReader = Annotated[Current, require(Action.VIEW_RAW_TEXT)]

_REFUSAL_STATUS = {
    UploadRefusal.EMPTY: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UploadRefusal.UNSUPPORTED_TYPE: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    UploadRefusal.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    UploadRefusal.QUOTA_EXCEEDED: status.HTTP_409_CONFLICT,
    UploadRefusal.UPLOADS_DISABLED: status.HTTP_503_SERVICE_UNAVAILABLE,
}
_LIMITER_RETRY_AFTER_SECONDS = 5
_TOO_LARGE = "The file is larger than this deployment accepts."

_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {FILE_FIELD: {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


def _refused(exc: DocumentRefusedError) -> HTTPException:
    return HTTPException(_REFUSAL_STATUS[exc.reason], str(exc))


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document: it is scanned, read and masked, then waits for review",
    openapi_extra=_UPLOAD_BODY,
    responses={
        409: {"description": "The project's document quota is full"},
        413: {"description": "The file is larger than the deployment accepts"},
        415: {"description": "Not multipart, or not a PDF, DOCX, TXT or MD file"},
        429: {"description": "Too many uploads lately; see Retry-After"},
        503: {"description": "Uploads are switched off by the operator"},
    },
)
async def upload_document(
    project_id: UUID,
    request: Request,
    response: Response,
    current: _Owner,
    documents: DocumentsDep,
) -> DocumentOut:
    """One file in a multipart field named `file`. Its type is read from its bytes.

    Returns at once with status `uploaded`; a worker does the rest. Follow it by
    listing the project's documents.
    """
    try:
        await documents.admit_upload(current, project_id)
        body = await read_capped(
            request.stream(), upload_body_limit(documents.policy.max_file_bytes)
        )
        upload = parse_single_file(request.headers.get("content-type", ""), body)
        view = await documents.upload(
            current, project_id, upload.filename, upload.content, request_id(request)
        )
    except DocumentRefusedError as exc:
        raise _refused(exc) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _TOO_LARGE) from exc
    except UploadFormatError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except RateLimiterUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Uploads are briefly unavailable. Try again shortly.",
            headers={"Retry-After": str(_LIMITER_RETRY_AFTER_SECONDS)},
        ) from exc
    response.headers["Location"] = f"{request.url.path.rstrip('/')}/{view.document.id}"
    return DocumentOut.from_view(view)


@router.get("", summary="The project's documents, their status, and the upload limits")
async def list_documents(
    project_id: UUID, current: _Member, documents: DocumentsDep
) -> DocumentListOut:
    return DocumentListOut.from_listing(await documents.list(current, project_id))


@router.get("/{document_id}", summary="One document and its status")
async def get_document(
    project_id: UUID, document_id: UUID, current: _Member, documents: DocumentsDep
) -> DocumentOut:
    return DocumentOut.from_view(await documents.get(current, project_id, document_id))


@router.get(
    "/{document_id}/masked",
    summary="What the model will see: the masked text, and what each placeholder hides",
)
async def get_masked_document(
    project_id: UUID, document_id: UUID, current: _Member, documents: DocumentsDep
) -> MaskedDocumentOut:
    """For any member. 404 until the document has been masked."""
    return MaskedDocumentOut.from_view(await documents.masked(current, project_id, document_id))


@router.get(
    "/{document_id}/review",
    summary="The masking review: original text and every mask. Owners only; audited",
)
async def get_document_review(
    project_id: UUID,
    document_id: UUID,
    request: Request,
    current: _RawTextReader,
    documents: DocumentsDep,
) -> DocumentReviewOut:
    """Shows raw text, so each request writes an audit event (ids only)."""
    view = await documents.review(current, project_id, document_id, request_id(request))
    return DocumentReviewOut.from_view(view)


@router.post(
    "/{document_id}/masks",
    summary="Mask a range of the original text the detectors missed",
    responses={409: {"description": "The masking changed since `version`, or is decided"}},
)
async def add_document_mask(
    project_id: UUID,
    document_id: UUID,
    payload: MaskIn,
    request: Request,
    current: _RawTextReader,
    documents: DocumentsDep,
) -> DocumentReviewOut:
    """A new masked version: the document needs review again, even if it was approved."""
    try:
        view = await documents.add_mask(
            current,
            project_id,
            document_id,
            version=payload.version,
            start=payload.start,
            end=payload.end,
            entity_type=payload.entity_type,
            request_id=request_id(request),
        )
    except DocumentStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return DocumentReviewOut.from_view(view)


@router.post(
    "/{document_id}/unmask",
    summary="Remove a mask that is not personal data, with a reason",
    responses={409: {"description": "The masking changed since `version`, or is decided"}},
)
async def remove_document_mask(
    project_id: UUID,
    document_id: UUID,
    payload: UnmaskIn,
    request: Request,
    current: _RawTextReader,
    documents: DocumentsDep,
) -> DocumentReviewOut:
    """Not for project rules or detected structured identifiers. The reason is kept sealed."""
    try:
        view = await documents.remove_mask(
            current,
            project_id,
            document_id,
            version=payload.version,
            start=payload.start,
            end=payload.end,
            reason=payload.reason,
            request_id=request_id(request),
        )
    except DocumentStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return DocumentReviewOut.from_view(view)


async def _decide(
    documents: DocumentsDep,
    current: Current,
    project_id: UUID,
    document_id: UUID,
    decision: DocumentDecision,
    version: int,
    request: Request,
) -> DocumentOut:
    try:
        view = await documents.decide(
            current,
            project_id,
            document_id,
            decision,
            version=version,
            request_id=request_id(request),
        )
    except DocumentStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return DocumentOut.from_view(view)


@router.post(
    "/{document_id}/approve",
    summary="Approve the masked version you reviewed: it may now be used as evidence",
    responses={409: {"description": "Not waiting for review, or the masking changed"}},
)
async def approve_document(
    project_id: UUID,
    document_id: UUID,
    payload: DecisionIn,
    request: Request,
    current: _Owner,
    documents: DocumentsDep,
) -> DocumentOut:
    """Records who approved which masked version, and when. Deciding the last document
    waiting for review resumes any run paused at its privacy review."""
    return await _decide(
        documents,
        current,
        project_id,
        document_id,
        DocumentDecision.APPROVE,
        payload.version,
        request,
    )


@router.post(
    "/{document_id}/reject",
    summary="Reject the masked version you reviewed: it is never used",
    responses={409: {"description": "Not waiting for review, or the masking changed"}},
)
async def reject_document(
    project_id: UUID,
    document_id: UUID,
    payload: DecisionIn,
    request: Request,
    current: _Owner,
    documents: DocumentsDep,
) -> DocumentOut:
    return await _decide(
        documents,
        current,
        project_id,
        document_id,
        DocumentDecision.REJECT,
        payload.version,
        request,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document: its file, raw text and masked text",
)
async def delete_document(
    project_id: UUID,
    document_id: UUID,
    request: Request,
    current: _Owner,
    documents: DocumentsDep,
) -> None:
    """A brief written earlier keeps the masked passages it cited."""
    await documents.delete(current, project_id, document_id, request_id(request))
