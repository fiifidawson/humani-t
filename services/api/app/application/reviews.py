"""Reviews: create one inside a project, list them, read one.

The question is sealed under the project's key in the same transaction that
stores the review, and leaves this module in the clear only in a return value.
The audit event records ids only, and is the last write.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.membership import (
    REVIEW_NOT_FOUND,
    require_member,
    require_review_member,
)
from app.domain.accounts import NotFoundError
from app.domain.encryption import EncryptionContext
from app.domain.projects import Action
from app.domain.reviews import Review, ReviewStatus, normalize_question
from app.storage.base import StorageProvider, UnitOfWork

# Table "reviews", column "question"; the row is the review.
_QUESTION_PURPOSE = "reviews.question"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReviewView:
    """A review with its question in the clear, for a member of its project."""

    id: UUID
    project_id: UUID
    question: str
    status: ReviewStatus
    created_at: datetime
    created_by: UUID
    created_by_name: str


# As the members list shows an account that no longer exists.
_UNKNOWN_AUTHOR = "Unknown account"


class ReviewService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._clock = clock

    async def create(
        self, current: Current, project_id: UUID, question: str, request_id: str | None = None
    ) -> ReviewView:
        """Raises `ValueError` for an unusable question, after the membership check."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow,
                current.user.id,
                project_id,
                Action.EDIT_REVIEW,
                not_found="No such project.",
            )
            question = normalize_question(question)
            review_id = uuid.uuid4()
            review = Review(
                id=review_id,
                project_id=project_id,
                question_sealed=await self._encryption.encrypt(
                    uow, question_context(project_id, review_id), question.encode()
                ),
                status=ReviewStatus.NEW,
                created_at=self._clock(),
                created_by=current.user.id,
            )
            await uow.reviews.add(review)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "review.created",
                "review",
                str(review_id),
                scope_id=project_id,
            )
            await uow.commit()
        return _view(review, question, current.user.display_name)

    async def list_for_project(self, current: Current, project_id: UUID) -> list[ReviewView]:
        """The project's reviews, newest first."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow,
                current.user.id,
                project_id,
                Action.READ_PROJECT,
                not_found="No such project.",
            )
            return [
                await self._open(uow, r) for r in await uow.reviews.list_for_project(project_id)
            ]

    async def authorize(self, current: Current, review_id: UUID, action: Action) -> None:
        """Check access without acting, so the HTTP layer refuses before reading a body."""
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, action)

    async def get(self, current: Current, project_id: UUID, review_id: UUID) -> ReviewView:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow,
                current.user.id,
                project_id,
                Action.READ_PROJECT,
                not_found="No such project.",
            )
            review = await uow.reviews.get(review_id)
            if review is None or review.project_id != project_id:
                raise NotFoundError(REVIEW_NOT_FOUND)
            return await self._open(uow, review)

    async def _open(self, uow: UnitOfWork, review: Review) -> ReviewView:
        question = await self._encryption.decrypt(
            uow, question_context(review.project_id, review.id), review.question_sealed
        )
        author = await uow.users.get(review.created_by)
        name = _UNKNOWN_AUTHOR if author is None else author.display_name
        return _view(review, question.decode(), name)


def _view(review: Review, question: str, author_name: str) -> ReviewView:
    return ReviewView(
        review.id,
        review.project_id,
        question,
        review.status,
        review.created_at,
        review.created_by,
        author_name,
    )


def question_context(project_id: UUID, review_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _QUESTION_PURPOSE, str(review_id))
