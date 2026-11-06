"""Follow-up questions in threads, and their answers, as rules with no I/O.

A member asks a question about a review's evidence (UI_UX_SPEC §6.12). The
question is masked before it is stored, and answered in the background: its
record moves pending → answering (under a worker's lease) → answered or
failed, and a lapsed lease is claimed again up to `MAX_ATTEMPTS` times, like a
run. Questions in one thread are numbered from 1 and answered one at a time,
because a follow-up reads the answers before it.

An answer is claims citing passages that were retrieved, checked by code like
the brief's (`check_answer`); an answer with no claims means nothing in the
project answers the question. Storage, the model and masking live elsewhere.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.briefs import Claim, Problem, check_claims
from app.domain.runs import RunConflictError

MAX_QUESTION_LENGTH = 2000
MAX_ANSWER_CLAIMS = 6
# A question whose worker dies is tried once more, then failed: often enough
# to ride out a deploy, rarely enough that a question that kills its worker
# cannot take one down forever.
MAX_ATTEMPTS = 2
# How many earlier questions and answers of the thread a follow-up carries.
CONTEXT_TURNS = 3

NOTHING_FOUND = "Nothing in this project answers that."
INTERRUPTED = "The answer was interrupted twice, so it was given up. Ask again."


class QuestionConflictError(RunConflictError):
    """The thread is not in a state that allows this question. The message is safe to show."""


class QuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERING = "answering"
    ANSWERED = "answered"
    FAILED = "failed"


def normalize_question(text: str) -> str:
    """The question as it will be masked: trimmed, inner whitespace collapsed."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("The question must not be blank")
    if len(cleaned) > MAX_QUESTION_LENGTH:
        raise ValueError(f"The question must be at most {MAX_QUESTION_LENGTH} characters")
    return cleaned


def _require_aware(owner: str, **timestamps: datetime | None) -> None:
    for name, value in timestamps.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Thread:
    """A line of questioning about one review, started by one member."""

    id: UUID
    project_id: UUID
    review_id: UUID
    created_at: datetime
    created_by: UUID

    def __post_init__(self) -> None:
        _require_aware("Thread", created_at=self.created_at)


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    """One question as storage holds it: ids and its state in the clear, its text sealed.

    `question_sealed` holds the masked question and what was masked, by type;
    `answer_sealed` the answer, the evidence it drew on and its agent steps.
    `error` is a fixed sentence for a person, never an exception's text.
    """

    id: UUID
    thread_id: UUID
    project_id: UUID
    review_id: UUID
    position: int
    asked_by: UUID
    asked_at: datetime
    status: QuestionStatus
    question_sealed: bytes
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    answered_at: datetime | None = None
    model_id: str | None = None
    answer_sealed: bytes | None = None
    error: str | None = None
    # Optimistic locking, as for runs.
    version: int = 0

    def __post_init__(self) -> None:
        _require_aware(
            "QuestionRecord",
            asked_at=self.asked_at,
            lease_expires_at=self.lease_expires_at,
            answered_at=self.answered_at,
        )
        if self.position < 1:
            raise ValueError("QuestionRecord.position counts from 1")
        if not self.question_sealed:
            raise ValueError("QuestionRecord.question_sealed must not be empty")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("QuestionRecord.lease_owner and lease_expires_at are set together")
        is_answered = self.status is QuestionStatus.ANSWERED
        if is_answered != (self.answer_sealed is not None):
            raise ValueError("QuestionRecord.answer_sealed is set exactly when answered")
        if (self.status is QuestionStatus.FAILED) != bool(self.error):
            raise ValueError("QuestionRecord.error is set exactly when failed")
        if not 0 <= self.attempts <= MAX_ATTEMPTS:
            raise ValueError(f"QuestionRecord.attempts must be between 0 and {MAX_ATTEMPTS}")

    @property
    def is_open(self) -> bool:
        """Still to be answered: a follow-up in its thread must wait."""
        return self.status in (QuestionStatus.PENDING, QuestionStatus.ANSWERING)

    def has_lapsed(self, now: datetime) -> bool:
        return (
            self.status is QuestionStatus.ANSWERING
            and self.lease_expires_at is not None
            and now > self.lease_expires_at
        )

    def can_be_claimed(self, now: datetime) -> bool:
        if self.status is QuestionStatus.PENDING:
            return True
        return self.has_lapsed(now) and self.attempts < MAX_ATTEMPTS

    def claimed(self, worker_id: str, now: datetime, lease: timedelta) -> QuestionRecord:
        if not self.can_be_claimed(now):
            raise ValueError("QuestionRecord cannot be claimed in its state")
        return replace(
            self,
            status=QuestionStatus.ANSWERING,
            attempts=self.attempts + 1,
            lease_owner=worker_id,
            lease_expires_at=now + lease,
        )

    def answered(self, answer_sealed: bytes, model_id: str, at: datetime) -> QuestionRecord:
        return replace(
            self,
            status=QuestionStatus.ANSWERED,
            answer_sealed=answer_sealed,
            model_id=model_id,
            answered_at=at,
            lease_owner=None,
            lease_expires_at=None,
        )

    def failed(self, error: str, at: datetime) -> QuestionRecord:
        return replace(
            self,
            status=QuestionStatus.FAILED,
            error=error,
            answered_at=at,
            lease_owner=None,
            lease_expires_at=None,
        )


@dataclass(frozen=True, slots=True)
class Answer:
    """Claims answering a question, in reading order. None means nothing answers it."""

    claims: tuple[Claim, ...]

    def __post_init__(self) -> None:
        if len(self.claims) > MAX_ANSWER_CLAIMS:
            raise ValueError(f"Answer.claims must hold at most {MAX_ANSWER_CLAIMS} claims")

    @property
    def nothing_found(self) -> bool:
        return not self.claims

    def placed_claims(self) -> tuple[tuple[str, Claim], ...]:
        return tuple((f"claims/{i}", claim) for i, claim in enumerate(self.claims))


def check_answer(answer: Answer, retrieved_tags: Collection[str]) -> tuple[Problem, ...]:
    """The brief's citation check, over an answer's claims."""
    return check_claims(answer.placed_claims(), retrieved_tags)
