"""Follow-up questions and their threads, as rules with no I/O (UI_UX_SPEC §6.12, §5.3.9).

A question is asked in a thread, answered in the background, and its record
moves pending → answering → answered or failed. An answer is claims citing
retrieved passages, or "nothing in this project answers that".
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.briefs import Citation, Claim, ClaimSupport, SourceKind
from app.domain.questions import (
    MAX_ATTEMPTS,
    Answer,
    QuestionRecord,
    QuestionStatus,
    Thread,
    check_answer,
    normalize_question,
)

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)


def evidence(text: str, *tags: str) -> Claim:
    return Claim(
        text, ClaimSupport.EVIDENCE, tuple(Citation(t, SourceKind.PAPER) for t in tags)
    )


def record(**changes: Any) -> QuestionRecord:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "review_id": uuid.uuid4(),
        "position": 1,
        "asked_by": uuid.uuid4(),
        "asked_at": NOW,
        "status": QuestionStatus.PENDING,
        "question_sealed": b"sealed",
    }
    values.update(changes)
    return QuestionRecord(**values)


class TestTheQuestion:
    def test_a_question_is_trimmed_and_its_spaces_collapsed(self):
        assert normalize_question("  Do  prices\nrise? ") == "Do prices rise?"

    @pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
    def test_a_blank_or_overlong_question_is_refused(self, text: str):
        with pytest.raises(ValueError, match="question"):
            normalize_question(text)


class TestTheLifecycle:
    def test_a_worker_claims_a_pending_question_under_a_lease(self):
        claimed = record().claimed("worker-a", NOW, LEASE)

        assert claimed.status is QuestionStatus.ANSWERING
        assert (claimed.lease_owner, claimed.lease_expires_at) == ("worker-a", NOW + LEASE)
        assert claimed.attempts == 1

    def test_an_answered_question_keeps_its_answer_and_gives_up_the_lease(self):
        done = record().claimed("w", NOW, LEASE).answered(b"answer", "qwen3.5:9b", NOW)

        assert done.status is QuestionStatus.ANSWERED
        assert (done.answer_sealed, done.lease_owner) == (b"answer", None)
        assert not done.is_open

    def test_a_failed_question_says_why_in_plain_words(self):
        failed = (
            record().claimed("w", NOW, LEASE).failed("The model server isn't answering.", NOW)
        )

        assert failed.status is QuestionStatus.FAILED
        assert failed.error == "The model server isn't answering."

    def test_an_answered_question_cannot_be_claimed_again(self):
        done = record().claimed("w", NOW, LEASE).answered(b"answer", "m", NOW)

        with pytest.raises(ValueError, match="QuestionRecord"):
            done.claimed("w", NOW, LEASE)

    def test_a_lapsed_lease_can_be_claimed_again_until_the_attempts_run_out(self):
        first = record().claimed("w", NOW, LEASE)
        later = NOW + LEASE + timedelta(seconds=1)

        assert first.can_be_claimed(later)
        again = first.claimed("v", later, LEASE)
        assert again.attempts == MAX_ATTEMPTS
        assert not again.can_be_claimed(later + LEASE + timedelta(seconds=1))

    def test_an_answered_record_must_hold_its_answer(self):
        with pytest.raises(ValueError, match="answer_sealed"):
            record(status=QuestionStatus.ANSWERED, answered_at=NOW)

    def test_positions_count_from_one(self):
        with pytest.raises(ValueError, match="position"):
            record(position=0)


class TestAnswers:
    def test_an_answer_with_no_claims_means_nothing_answers_it(self):
        assert Answer(()).nothing_found

    def test_an_answer_holds_at_most_six_claims(self):
        with pytest.raises(ValueError, match=r"Answer\.claims"):
            Answer(tuple(evidence(f"claim {i}", "P1") for i in range(7)))

    def test_a_claim_citing_a_passage_never_retrieved_is_a_problem(self):
        answer = Answer((evidence("Prices rose", "P1"), evidence("Prices fell", "P9")))

        problems = check_answer(answer, frozenset({"P1"}))

        assert [(p.code, p.where) for p in problems] == [("citation_not_retrieved", "claims/1")]

    def test_an_answer_naming_a_passage_on_every_claim_has_no_problems(self):
        assert check_answer(Answer((evidence("Prices rose", "P1"),)), {"P1"}) == ()


class TestThread:
    def test_a_thread_belongs_to_one_review_and_project(self):
        thread = Thread(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), NOW, uuid.uuid4())

        assert replace(thread).review_id == thread.review_id

    def test_its_timestamp_is_aware(self):
        with pytest.raises(ValueError, match=r"Thread\.created_at"):
            Thread(
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), NOW.replace(tzinfo=None), uuid.uuid4()
            )
