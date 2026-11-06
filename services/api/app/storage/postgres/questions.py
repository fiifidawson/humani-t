"""PostgreSQL repository for follow-up questions and their threads.

The questions table is also the queue of questions waiting for an answer.
Workers claim with `SELECT … FOR UPDATE SKIP LOCKED`, as for runs (ADR-0003),
and every change is version-checked, so two workers never answer one question.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.errors import ConcurrentUpdateError
from app.domain.questions import MAX_ATTEMPTS, QuestionRecord, QuestionStatus, Thread
from app.storage.postgres.tables import question_threads, questions

_ANSWERING = QuestionStatus.ANSWERING.value


def _to_thread(row: Row[Any]) -> Thread:
    return Thread(row.id, row.project_id, row.review_id, row.created_at, row.created_by)


def _to_question(row: Row[Any]) -> QuestionRecord:
    return QuestionRecord(
        id=row.id,
        thread_id=row.thread_id,
        project_id=row.project_id,
        review_id=row.review_id,
        position=row.position,
        asked_by=row.asked_by,
        asked_at=row.asked_at,
        status=QuestionStatus(row.status),
        question_sealed=row.question_sealed,
        attempts=row.attempts,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        answered_at=row.answered_at,
        model_id=row.model_id,
        answer_sealed=row.answer_sealed,
        error=row.error,
        version=row.version,
    )


def _changing(q: QuestionRecord) -> dict[str, Any]:
    return {
        "status": q.status.value,
        "attempts": q.attempts,
        "lease_owner": q.lease_owner,
        "lease_expires_at": q.lease_expires_at,
        "answered_at": q.answered_at,
        "model_id": q.model_id,
        "answer_sealed": q.answer_sealed,
        "error": q.error,
    }


class PostgresQuestionRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def add_thread(self, thread: Thread, /) -> None:
        await self._connection.execute(
            insert(question_threads).values(
                id=thread.id,
                project_id=thread.project_id,
                review_id=thread.review_id,
                created_at=thread.created_at,
                created_by=thread.created_by,
            )
        )

    async def get_thread(self, thread_id: UUID) -> Thread | None:
        row = (
            await self._connection.execute(
                select(question_threads).where(question_threads.c.id == thread_id)
            )
        ).one_or_none()
        return None if row is None else _to_thread(row)

    async def list_threads(self, review_id: UUID) -> list[Thread]:
        result = await self._connection.execute(
            select(question_threads)
            .where(question_threads.c.review_id == review_id)
            .order_by(question_threads.c.created_at.desc(), question_threads.c.id.desc())
        )
        return [_to_thread(row) for row in result]

    async def add(self, question: QuestionRecord, /) -> None:
        try:
            # A savepoint, so a lost race on the position leaves the transaction usable.
            async with self._connection.begin_nested():
                await self._connection.execute(
                    insert(questions).values(
                        id=question.id,
                        thread_id=question.thread_id,
                        project_id=question.project_id,
                        review_id=question.review_id,
                        position=question.position,
                        asked_by=question.asked_by,
                        asked_at=question.asked_at,
                        question_sealed=question.question_sealed,
                        version=question.version,
                        **_changing(question),
                    )
                )
        except IntegrityError as exc:
            raise ConcurrentUpdateError(
                f"thread {question.thread_id} already has question {question.position}"
            ) from exc

    async def _one(self, statement: Any) -> QuestionRecord | None:
        row = (await self._connection.execute(statement)).one_or_none()
        return None if row is None else _to_question(row)

    async def get(self, question_id: UUID) -> QuestionRecord | None:
        return await self._one(select(questions).where(questions.c.id == question_id))

    async def list_for_thread(self, thread_id: UUID) -> list[QuestionRecord]:
        result = await self._connection.execute(
            select(questions)
            .where(questions.c.thread_id == thread_id)
            .order_by(questions.c.position)
        )
        return [_to_question(row) for row in result]

    async def save(self, question: QuestionRecord, /) -> QuestionRecord:
        result = await self._connection.execute(
            update(questions)
            .where(questions.c.id == question.id, questions.c.version == question.version)
            .values(version=question.version + 1, **_changing(question))
            .returning(questions)
        )
        row = result.one_or_none()
        if row is None:
            raise ConcurrentUpdateError(f"question {question.id} changed concurrently")
        return _to_question(row)

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> QuestionRecord | None:
        lapsed = and_(
            questions.c.status == _ANSWERING,
            questions.c.lease_expires_at < now,
            questions.c.attempts < MAX_ATTEMPTS,
        )
        oldest = await self._one(
            select(questions)
            .where(or_(questions.c.status == QuestionStatus.PENDING.value, lapsed))
            .order_by(questions.c.asked_at, questions.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if oldest is None:
            return None
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, question_id: UUID, worker_id: str, until: datetime) -> bool:
        result = await self._connection.execute(
            update(questions)
            .where(
                questions.c.id == question_id,
                questions.c.status == _ANSWERING,
                questions.c.lease_owner == worker_id,
            )
            .values(lease_expires_at=until, version=questions.c.version + 1)
        )
        return result.rowcount == 1

    async def lock_exhausted(self, now: datetime, limit: int) -> list[QuestionRecord]:
        result = await self._connection.execute(
            select(questions)
            .where(
                questions.c.status == _ANSWERING,
                questions.c.lease_expires_at < now,
                questions.c.attempts >= MAX_ATTEMPTS,
            )
            .order_by(questions.c.asked_at, questions.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_to_question(row) for row in result]
