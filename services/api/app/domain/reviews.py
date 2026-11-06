"""Reviews: a question inside a project, and the states it moves through.

A review's status follows UI_UX_SPEC §11.1: the planning states (new, clarifying,
drafting, changes requested, accepted) and then the state of its latest run.
The transition table below is the whole state machine; nothing else may move a
review from one status to another.

This module holds no plaintext: the question lives here only as ciphertext
sealed under the project's key, which the application layer seals and opens.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

MAX_QUESTION_LENGTH = 2000

# Characters that can reorder or hide the text around them.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
# A question may run over several lines; no other control character belongs in one.
_ALLOWED_CONTROLS = frozenset("\n\t")


class InvalidTransitionError(Exception):
    """The change does not follow from the current state. Shown to the user as a conflict."""


class ReviewStatus(StrEnum):
    NEW = "new"
    CLARIFYING = "clarifying"
    DRAFTING = "drafting"
    CHANGES_REQUESTED = "changes_requested"
    ACCEPTED = "accepted"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewTrigger(StrEnum):
    CLARIFY = "clarify"
    DRAFT = "draft"
    REQUEST_CHANGES = "request_changes"
    ACCEPT = "accept"
    START_RUN = "start_run"
    PAUSE = "pause"
    RESUME = "resume"
    FINISH = "finish"
    FAIL = "fail"
    CANCEL = "cancel"


_S = ReviewStatus
_T = ReviewTrigger

# A run may start from any state in which no run is going. Before acceptance,
# the run's first stage asks for the vocabulary through a `vocabulary` gate
# (UI_UX_SPEC §5.3.6); after a run has ended, the review may run again.
_CAN_START_A_RUN = (
    _S.NEW,
    _S.CLARIFYING,
    _S.DRAFTING,
    _S.CHANGES_REQUESTED,
    _S.ACCEPTED,
    _S.DONE,
    _S.FAILED,
    _S.CANCELLED,
)

_TRANSITIONS: Mapping[tuple[ReviewStatus, ReviewTrigger], ReviewStatus] = {
    (_S.NEW, _T.CLARIFY): _S.CLARIFYING,
    (_S.NEW, _T.DRAFT): _S.DRAFTING,
    (_S.CLARIFYING, _T.DRAFT): _S.DRAFTING,
    (_S.CHANGES_REQUESTED, _T.DRAFT): _S.DRAFTING,
    (_S.DRAFTING, _T.REQUEST_CHANGES): _S.CHANGES_REQUESTED,
    # An accepted plan never changes; asking for changes drafts its next version.
    **{
        (status, _T.REQUEST_CHANGES): _S.CHANGES_REQUESTED
        for status in (_S.ACCEPTED, _S.DONE, _S.FAILED, _S.CANCELLED)
    },
    (_S.DRAFTING, _T.ACCEPT): _S.ACCEPTED,
    **{(status, _T.START_RUN): _S.RUNNING for status in _CAN_START_A_RUN},
    (_S.RUNNING, _T.PAUSE): _S.WAITING,
    (_S.WAITING, _T.RESUME): _S.RUNNING,
    (_S.RUNNING, _T.FINISH): _S.DONE,
    (_S.RUNNING, _T.FAIL): _S.FAILED,
    (_S.RUNNING, _T.CANCEL): _S.CANCELLED,
    (_S.WAITING, _T.CANCEL): _S.CANCELLED,
}


def next_review_status(status: ReviewStatus, trigger: ReviewTrigger) -> ReviewStatus:
    """The status `trigger` leads to from `status`, or `InvalidTransitionError`."""
    following = _TRANSITIONS.get((status, trigger))
    if following is None:
        raise InvalidTransitionError("This review is not in a state that allows this.")
    return following


def normalize_question(question: str) -> str:
    """Trim the question and refuse one that is blank, too long or hides its own text."""
    trimmed = question.strip()
    if not trimmed:
        raise ValueError("Review.question must not be blank")
    if len(trimmed) > MAX_QUESTION_LENGTH:
        raise ValueError(f"Review.question must be at most {MAX_QUESTION_LENGTH} characters")
    if any(
        ch in _BIDI_CONTROLS
        or (unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS)
        for ch in trimmed
    ):
        raise ValueError("Review.question must not contain control characters")
    return trimmed


@dataclass(frozen=True, slots=True)
class Review:
    id: UUID
    project_id: UUID
    question_sealed: bytes
    status: ReviewStatus
    created_at: datetime
    created_by: UUID
    # Optimistic locking: a stale copy can never overwrite a newer one.
    version: int = 0

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("Review.created_at must be timezone-aware")
        if not self.question_sealed:
            raise ValueError("Review.question_sealed must not be empty")

    def transitioned(self, trigger: ReviewTrigger) -> Review:
        return replace(self, status=next_review_status(self.status, trigger))
