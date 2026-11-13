"""PostgreSQL repository for the planning conversation, plan versions and privacy receipts.

Accepting is one conditional `UPDATE`: it changes the row only if the version
is the review's latest and not yet accepted, so of two concurrent accepts the
second re-checks after the first commits and changes nothing. A trigger
(migration 0008) refuses any other change to a stored version.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, exists, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.errors import ConcurrentUpdateError
from app.domain.plans import (
    AuthorKind,
    ModelCallPurpose,
    PlanAuthor,
    PlanMode,
    StoredConversation,
    StoredPlanVersion,
    StoredReceipt,
)
from app.domain.vocabulary import VocabularyOrigin
from app.storage.postgres.tables import plan_conversations, plan_receipts, plan_versions


def _to_conversation(row: Row[Any]) -> StoredConversation:
    return StoredConversation(
        review_id=row.review_id,
        project_id=row.project_id,
        mode=PlanMode(row.mode),
        created_at=row.created_at,
        created_by=row.created_by,
        body_sealed=row.body_sealed,
        version=row.version,
    )


def _to_version(row: Row[Any]) -> StoredPlanVersion:
    return StoredPlanVersion(
        review_id=row.review_id,
        project_id=row.project_id,
        version=row.version,
        origin=VocabularyOrigin(row.origin),
        author=PlanAuthor(AuthorKind(row.author_kind), row.author_id),
        created_at=row.created_at,
        body_sealed=row.body_sealed,
        accepted_at=row.accepted_at,
        accepted_by=row.accepted_by,
    )


def _to_receipt(row: Row[Any]) -> StoredReceipt:
    return StoredReceipt(
        id=row.id,
        review_id=row.review_id,
        project_id=row.project_id,
        purpose=ModelCallPurpose(row.purpose),
        created_at=row.created_at,
        created_by=row.created_by,
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        is_delivered=row.is_delivered,
        body_sealed=row.body_sealed,
        plan_version=row.plan_version,
        change_request_id=row.change_request_id,
        is_masked=row.is_masked,
    )


class PostgresPlanRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get_conversation(self, review_id: UUID) -> StoredConversation | None:
        row = (
            await self._connection.execute(
                select(plan_conversations).where(plan_conversations.c.review_id == review_id)
            )
        ).one_or_none()
        return None if row is None else _to_conversation(row)

    async def add_conversation(self, conversation: StoredConversation, /) -> None:
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(
                    insert(plan_conversations).values(
                        review_id=conversation.review_id,
                        project_id=conversation.project_id,
                        mode=conversation.mode.value,
                        created_at=conversation.created_at,
                        created_by=conversation.created_by,
                        body_sealed=conversation.body_sealed,
                        version=conversation.version,
                    )
                )
        except IntegrityError as exc:
            if await self.get_conversation(conversation.review_id) is None:
                raise
            raise ConcurrentUpdateError(
                f"review {conversation.review_id} already has a planning conversation"
            ) from exc

    async def save_conversation(
        self, conversation: StoredConversation, /
    ) -> StoredConversation:
        result = await self._connection.execute(
            update(plan_conversations)
            .where(
                plan_conversations.c.review_id == conversation.review_id,
                plan_conversations.c.version == conversation.version,
            )
            .values(
                mode=conversation.mode.value,
                body_sealed=conversation.body_sealed,
                version=conversation.version + 1,
            )
            .returning(*plan_conversations.c)
        )
        row = result.one_or_none()
        if row is None:
            raise ConcurrentUpdateError(
                f"the conversation of review {conversation.review_id} changed concurrently"
            )
        return _to_conversation(row)

    async def add_version(self, version: StoredPlanVersion, /) -> None:
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(
                    insert(plan_versions).values(
                        review_id=version.review_id,
                        version=version.version,
                        project_id=version.project_id,
                        origin=version.origin.value,
                        author_kind=version.author.kind.value,
                        author_id=version.author.user_id,
                        created_at=version.created_at,
                        accepted_at=version.accepted_at,
                        accepted_by=version.accepted_by,
                        body_sealed=version.body_sealed,
                    )
                )
        except IntegrityError as exc:
            if await self.get_version(version.review_id, version.version) is None:
                raise
            raise ConcurrentUpdateError(
                f"review {version.review_id} already has plan version {version.version}"
            ) from exc

    async def get_version(self, review_id: UUID, version: int) -> StoredPlanVersion | None:
        row = (
            await self._connection.execute(
                select(plan_versions).where(
                    plan_versions.c.review_id == review_id, plan_versions.c.version == version
                )
            )
        ).one_or_none()
        return None if row is None else _to_version(row)

    async def latest_version(self, review_id: UUID) -> StoredPlanVersion | None:
        row = (
            await self._connection.execute(
                select(plan_versions)
                .where(plan_versions.c.review_id == review_id)
                .order_by(plan_versions.c.version.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else _to_version(row)

    async def latest_accepted(self, review_id: UUID) -> StoredPlanVersion | None:
        row = (
            await self._connection.execute(
                select(plan_versions)
                .where(
                    plan_versions.c.review_id == review_id,
                    plan_versions.c.accepted_at.is_not(None),
                )
                .order_by(plan_versions.c.version.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else _to_version(row)

    async def list_versions(self, review_id: UUID) -> list[StoredPlanVersion]:
        result = await self._connection.execute(
            select(plan_versions)
            .where(plan_versions.c.review_id == review_id)
            .order_by(plan_versions.c.version)
        )
        return [_to_version(row) for row in result]

    async def accept(self, review_id: UUID, version: int, by: UUID, at: datetime) -> bool:
        later = plan_versions.alias("later")
        newer_exists = exists(
            select(later.c.version).where(
                later.c.review_id == review_id, later.c.version > version
            )
        )
        result = await self._connection.execute(
            update(plan_versions)
            .where(
                plan_versions.c.review_id == review_id,
                plan_versions.c.version == version,
                plan_versions.c.accepted_at.is_(None),
                ~newer_exists,
            )
            .values(accepted_at=at, accepted_by=by)
            .returning(plan_versions.c.version)
        )
        return result.first() is not None

    async def add_receipt(self, receipt: StoredReceipt, /) -> None:
        await self._connection.execute(
            insert(plan_receipts).values(
                id=receipt.id,
                review_id=receipt.review_id,
                project_id=receipt.project_id,
                purpose=receipt.purpose.value,
                created_at=receipt.created_at,
                created_by=receipt.created_by,
                model_id=receipt.model_id,
                prompt_version=receipt.prompt_version,
                is_delivered=receipt.is_delivered,
                body_sealed=receipt.body_sealed,
                plan_version=receipt.plan_version,
                change_request_id=receipt.change_request_id,
                is_masked=receipt.is_masked,
            )
        )

    async def list_receipts(self, review_id: UUID) -> list[StoredReceipt]:
        result = await self._connection.execute(
            select(plan_receipts)
            .where(plan_receipts.c.review_id == review_id)
            .order_by(plan_receipts.c.created_at, plan_receipts.c.id)
        )
        return [_to_receipt(row) for row in result]
