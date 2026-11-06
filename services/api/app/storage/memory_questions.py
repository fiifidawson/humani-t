"""In-memory follow-up questions and threads.

Kept beside `memory.py`, as `memory_briefs.py` is: `memory.py` holds one
`QuestionState` and copies it with the rest, so questions commit and roll back
with everything else in the unit of work.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.errors import ConcurrentUpdateError
from app.domain.questions import MAX_ATTEMPTS, QuestionRecord, QuestionStatus, Thread


class QuestionState:
    def __init__(self) -> None:
        self.threads: dict[UUID, Thread] = {}
        self.questions: dict[UUID, QuestionRecord] = {}

    def copy(self) -> QuestionState:
        clone = QuestionState()
        clone.threads = dict(self.threads)
        clone.questions = dict(self.questions)
        return clone


class MemoryQuestionRepository:
    def __init__(self, state: QuestionState) -> None:
        self._state = state

    async def add_thread(self, thread: Thread, /) -> None:
        if thread.id in self._state.threads:
            raise ValueError(f"thread {thread.id} already exists")
        self._state.threads[thread.id] = thread

    async def get_thread(self, thread_id: UUID) -> Thread | None:
        return self._state.threads.get(thread_id)

    async def list_threads(self, review_id: UUID) -> list[Thread]:
        threads = [t for t in self._state.threads.values() if t.review_id == review_id]
        return sorted(threads, key=lambda t: (t.created_at, t.id), reverse=True)

    async def add(self, question: QuestionRecord, /) -> None:
        taken = any(
            (q.thread_id, q.position) == (question.thread_id, question.position)
            for q in self._state.questions.values()
        )
        if taken:
            raise ConcurrentUpdateError(
                f"thread {question.thread_id} already has question {question.position}"
            )
        if question.id in self._state.questions:
            raise ValueError(f"question {question.id} already exists")
        self._state.questions[question.id] = question

    async def get(self, question_id: UUID) -> QuestionRecord | None:
        return self._state.questions.get(question_id)

    async def list_for_thread(self, thread_id: UUID) -> list[QuestionRecord]:
        found = [q for q in self._state.questions.values() if q.thread_id == thread_id]
        return sorted(found, key=lambda q: q.position)

    async def save(self, question: QuestionRecord, /) -> QuestionRecord:
        stored = self._state.questions.get(question.id)
        if stored is None:
            raise ValueError(f"question {question.id} does not exist")
        if stored.version != question.version:
            raise ConcurrentUpdateError(f"question {question.id} changed concurrently")
        saved = replace(question, version=question.version + 1)
        self._state.questions[question.id] = saved
        return saved

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> QuestionRecord | None:
        waiting = [q for q in self._state.questions.values() if q.can_be_claimed(now)]
        if not waiting:
            return None
        oldest = min(waiting, key=lambda q: (q.asked_at, q.id))
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, question_id: UUID, worker_id: str, until: datetime) -> bool:
        stored = self._state.questions.get(question_id)
        if (
            stored is None
            or stored.status is not QuestionStatus.ANSWERING
            or stored.lease_owner != worker_id
        ):
            return False
        await self.save(replace(stored, lease_expires_at=until))
        return True

    async def lock_exhausted(self, now: datetime, limit: int) -> list[QuestionRecord]:
        stuck = [
            q
            for q in self._state.questions.values()
            if q.has_lapsed(now) and q.attempts >= MAX_ATTEMPTS
        ]
        return sorted(stuck, key=lambda q: (q.asked_at, q.id))[:limit]
