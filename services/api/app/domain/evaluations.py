"""Evaluations as rules with no I/O: the upload formats, blind judgements and their results.

An instance administrator uploads questions and, optionally, other systems' answers;
humani-t answers each question and a judge model compares each pair of answers, shown as
A and B in a random order. The record is also the job, claimed under a lease like a run.
Storage, masking and model calls belong in `application/evaluations.py`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID

from app.domain.briefs import CRITIC_DIMENSIONS, CriticScores
from app.domain.questions import normalize_question
from app.domain.timestamps import require_aware

HUMANI_T = "humani-t"
MAX_QUESTIONS = 50
MAX_OTHER_SYSTEMS = 2
MAX_ANSWER_LENGTH = 8000
MAX_SYSTEM_NAME_LENGTH = 60
MAX_REASON_LENGTH = 600
# As for questions: once more to ride out a deploy, then given up.
MAX_ATTEMPTS = 2


class EvaluationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class Verdict(StrEnum):
    """Which answer the judge preferred, by the letter it saw."""

    A = "a"
    B = "b"
    TIE = "tie"


def parse_question_file(text: str) -> tuple[str, ...]:
    """One question per line; blank lines are skipped. Errors name the line."""
    questions: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            questions.append(normalize_question(line))
        except ValueError as exc:
            raise ValueError(f"Line {number} of the question file: {exc}") from exc
    if not questions:
        raise ValueError("The question file has no questions. Put one question on each line.")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(
            f"The question file has {len(questions)} questions; it may hold at most "
            f"{MAX_QUESTIONS}."
        )
    return tuple(questions)


def parse_answer_file(text: str, *, expected: int, system: str) -> tuple[str, ...]:
    """JSON Lines, `{"answer": "…"}` per line, answering the questions in their order."""
    answers: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        answers.append(_answer_line(line, f"{system}, line {number}"))
    if len(answers) != expected:
        raise ValueError(
            f"{system} has {len(answers)} answers for {expected} questions. "
            "Give one answer per question, in the same order."
        )
    return tuple(answers)


def _answer_line(line: str, where: str) -> str:
    try:
        value: object = json.loads(line)
    except ValueError:
        value = None
    fields = cast(dict[str, object], value) if isinstance(value, dict) else {}
    answer = fields.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError(f'{where}: each line must be a JSON object with an "answer" text.')
    if len(answer) > MAX_ANSWER_LENGTH:
        raise ValueError(f"{where}: an answer may be at most {MAX_ANSWER_LENGTH} characters.")
    return answer.strip()


def system_names(names: Sequence[str]) -> tuple[str, ...]:
    """The other systems' names as shown in the results: trimmed, distinct, not humani-t's."""
    if len(names) > MAX_OTHER_SYSTEMS:
        raise ValueError(f"Compare humani-t with at most {MAX_OTHER_SYSTEMS} other systems.")
    cleaned = tuple(" ".join(name.split()) for name in names)
    seen: set[str] = {HUMANI_T}
    for name in cleaned:
        if not name or len(name) > MAX_SYSTEM_NAME_LENGTH:
            raise ValueError(
                f"A system's name must be 1 to {MAX_SYSTEM_NAME_LENGTH} characters."
            )
        if name.casefold() == HUMANI_T:
            raise ValueError(f"Another system may not be called {HUMANI_T}.")
        if name.casefold() in seen:
            raise ValueError(f"Use each system's name once: {name} is there twice.")
        seen.add(name.casefold())
    return cleaned


@dataclass(frozen=True, slots=True)
class OtherSystem:
    """Another system's answers, one per question, in the questions' order."""

    name: str
    answers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationUpload:
    """What an administrator uploads, parsed and checked, not yet masked."""

    project_id: UUID
    judge_model: str
    questions: tuple[str, ...]
    systems: tuple[OtherSystem, ...]

    def __post_init__(self) -> None:
        if not self.judge_model.strip():
            raise ValueError("EvaluationUpload.judge_model must not be blank")
        if not self.questions:
            raise ValueError("EvaluationUpload.questions must not be empty")
        system_names([s.name for s in self.systems])
        if any(len(s.answers) != len(self.questions) for s in self.systems):
            raise ValueError("EvaluationUpload: every system answers every question")

    @classmethod
    def from_files(
        cls,
        project_id: UUID,
        judge_model: str,
        question_file: str,
        others: Sequence[tuple[str, str]],
    ) -> EvaluationUpload:
        """Parse the question file and each `(name, answers file)`; errors name the line."""
        questions = parse_question_file(question_file)
        names = system_names([name for name, _ in others])
        systems = tuple(
            OtherSystem(name, parse_answer_file(text, expected=len(questions), system=name))
            for name, (_, text) in zip(names, others, strict=True)
        )
        return cls(project_id, judge_model.strip(), questions, systems)


@dataclass(frozen=True, slots=True)
class Judgement:
    """The judge's comparison of two answers to one question, as it saw them."""

    position: int
    system_a: str
    system_b: str
    scores_a: CriticScores
    scores_b: CriticScores
    verdict: Verdict
    reason: str

    def __post_init__(self) -> None:
        if self.position < 1:
            raise ValueError("Judgement.position counts from 1")
        if self.system_a == self.system_b:
            raise ValueError("Judgement.system_b must differ from system_a")
        if not self.reason.strip():
            raise ValueError("Judgement.reason must not be blank")

    def scores_of(self, system: str) -> CriticScores:
        return self.scores_a if system == self.system_a else self.scores_b

    def winner(self) -> str | None:
        """The system the judge preferred; `None` for a tie."""
        if self.verdict is Verdict.TIE:
            return None
        return self.system_a if self.verdict is Verdict.A else self.system_b


@dataclass(frozen=True, slots=True)
class MetricResult:
    metric: str
    first_mean: float
    second_mean: float

    @property
    def difference(self) -> float:
        return self.first_mean - self.second_mean


@dataclass(frozen=True, slots=True)
class PairResult:
    """Two systems over every question judged so far, from the first system's side."""

    first: str
    second: str
    judged: int
    metrics: tuple[MetricResult, ...]
    wins: int
    ties: int
    losses: int


def summarise(
    judgements: Sequence[Judgement], first: str, others: Sequence[str]
) -> tuple[PairResult, ...]:
    """One result per other system with anything judged; the order shown never matters."""
    results: list[PairResult] = []
    for second in others:
        pair = [j for j in judgements if {j.system_a, j.system_b} == {first, second}]
        if not pair:
            continue
        winners = [j.winner() for j in pair]
        metrics = tuple(
            MetricResult(
                metric,
                _mean([getattr(j.scores_of(first), metric) for j in pair]),
                _mean([getattr(j.scores_of(second), metric) for j in pair]),
            )
            for metric in CRITIC_DIMENSIONS
        )
        results.append(
            PairResult(
                first=first,
                second=second,
                judged=len(pair),
                metrics=metrics,
                wins=winners.count(first),
                ties=winners.count(None),
                losses=winners.count(second),
            )
        )
    return tuple(results)


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values)


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    """One evaluation as storage holds it: ids, counts and state in the clear, text sealed.

    `setup_sealed` holds the masked questions and the other systems' masked answers;
    `results_sealed` humani-t's answers and the judgements, from the first one answered.
    `error` is a fixed sentence for a person, never an exception's text.
    """

    id: UUID
    project_id: UUID
    review_id: UUID
    created_by: UUID
    created_at: datetime
    judge_model: str
    question_count: int
    system_count: int
    status: EvaluationStatus
    setup_sealed: bytes
    results_sealed: bytes | None = None
    answered_count: int = 0
    writer_model: str | None = None
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    # Optimistic locking, as for questions.
    version: int = 0

    def __post_init__(self) -> None:
        require_aware(
            "EvaluationRecord",
            created_at=self.created_at,
            lease_expires_at=self.lease_expires_at,
            finished_at=self.finished_at,
        )
        if not self.judge_model.strip():
            raise ValueError("EvaluationRecord.judge_model must not be blank")
        if not 1 <= self.question_count <= MAX_QUESTIONS:
            raise ValueError(
                f"EvaluationRecord.question_count must be between 1 and {MAX_QUESTIONS}"
            )
        if not 0 <= self.system_count <= MAX_OTHER_SYSTEMS:
            raise ValueError(
                f"EvaluationRecord.system_count must be between 0 and {MAX_OTHER_SYSTEMS}"
            )
        if not 0 <= self.answered_count <= self.question_count:
            raise ValueError("EvaluationRecord.answered_count must not pass question_count")
        if (self.answered_count > 0) != (self.results_sealed is not None):
            raise ValueError("EvaluationRecord.results_sealed is set once a question is done")
        if not self.setup_sealed:
            raise ValueError("EvaluationRecord.setup_sealed must not be empty")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError(
                "EvaluationRecord.lease_owner and lease_expires_at are set together"
            )
        if (self.status is EvaluationStatus.FAILED) != bool(self.error):
            raise ValueError("EvaluationRecord.error is set exactly when failed")
        if self.is_open == (self.finished_at is not None):
            raise ValueError("EvaluationRecord.finished_at is set exactly when it ended")
        if not 0 <= self.attempts <= MAX_ATTEMPTS:
            raise ValueError(f"EvaluationRecord.attempts must be between 0 and {MAX_ATTEMPTS}")

    @property
    def is_open(self) -> bool:
        return self.status in (EvaluationStatus.QUEUED, EvaluationStatus.RUNNING)

    def has_lapsed(self, now: datetime) -> bool:
        return (
            self.status is EvaluationStatus.RUNNING
            and self.lease_expires_at is not None
            and now > self.lease_expires_at
        )

    def can_be_claimed(self, now: datetime) -> bool:
        if self.status is EvaluationStatus.QUEUED:
            return True
        return self.has_lapsed(now) and self.attempts < MAX_ATTEMPTS

    def claimed(self, worker_id: str, now: datetime, lease: timedelta) -> EvaluationRecord:
        if not self.can_be_claimed(now):
            raise ValueError("EvaluationRecord cannot be claimed in its state")
        return replace(
            self,
            status=EvaluationStatus.RUNNING,
            attempts=self.attempts + 1,
            lease_owner=worker_id,
            lease_expires_at=now + lease,
        )

    def progressed(
        self, results_sealed: bytes, answered_count: int, writer_model: str
    ) -> EvaluationRecord:
        return replace(
            self,
            results_sealed=results_sealed,
            answered_count=answered_count,
            writer_model=writer_model,
        )

    def finished(self, at: datetime) -> EvaluationRecord:
        return replace(
            self,
            status=EvaluationStatus.FINISHED,
            finished_at=at,
            lease_owner=None,
            lease_expires_at=None,
        )

    def failed(self, error: str, at: datetime) -> EvaluationRecord:
        return replace(
            self,
            status=EvaluationStatus.FAILED,
            error=error,
            finished_at=at,
            lease_owner=None,
            lease_expires_at=None,
        )
