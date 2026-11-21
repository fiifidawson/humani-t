"""Evaluations as rules: the upload formats, a blind pairwise judgement, and the results.

A question file holds one question per line; another system's answers are JSON Lines in
the same order. A judgement is scored per answer on the brief's six quality checks, and the
results are read from the first system's side whatever order the judge saw.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.briefs import CRITIC_DIMENSIONS, CriticScores
from app.domain.evaluations import (
    HUMANI_T,
    MAX_OTHER_SYSTEMS,
    MAX_QUESTIONS,
    EvaluationRecord,
    EvaluationStatus,
    Judgement,
    Verdict,
    parse_answer_file,
    parse_question_file,
    summarise,
    system_names,
)

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=10)


def scores(value: int = 1, **overrides: int) -> CriticScores:
    values: dict[str, int] = dict.fromkeys(CRITIC_DIMENSIONS, value)
    values.update(overrides)
    return CriticScores(**values)


def answers_file(*answers: str) -> str:
    return "\n".join(json.dumps({"answer": a}) for a in answers)


def judgement(
    position: int = 1,
    *,
    a: str = HUMANI_T,
    b: str = "System B",
    scores_a: CriticScores | None = None,
    scores_b: CriticScores | None = None,
    verdict: Verdict = Verdict.A,
) -> Judgement:
    return Judgement(
        position=position,
        system_a=a,
        system_b=b,
        scores_a=scores_a or scores(),
        scores_b=scores_b or scores(),
        verdict=verdict,
        reason="A covers the price effect; B does not.",
    )


def record(**overrides: object) -> EvaluationRecord:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "review_id": uuid.uuid4(),
        "created_by": uuid.uuid4(),
        "created_at": NOW,
        "judge_model": "mistral-small:24b",
        "question_count": 2,
        "system_count": 1,
        "status": EvaluationStatus.QUEUED,
        "setup_sealed": b"sealed",
    }
    values.update(overrides)
    return EvaluationRecord(**values)  # type: ignore[arg-type]


class TestQuestionFile:
    def test_each_line_is_one_question_and_blank_lines_are_skipped(self):
        text = "Do cash transfers raise prices?\n\n  Which   markets recover first?  \n"

        assert parse_question_file(text) == (
            "Do cash transfers raise prices?",
            "Which markets recover first?",
        )

    def test_a_file_with_no_question_is_refused(self):
        with pytest.raises(ValueError, match="no questions"):
            parse_question_file("\n  \n")

    def test_a_file_with_too_many_questions_is_refused(self):
        text = "\n".join(f"Question {n}?" for n in range(MAX_QUESTIONS + 1))

        with pytest.raises(ValueError, match=f"at most {MAX_QUESTIONS}"):
            parse_question_file(text)

    def test_a_question_that_is_too_long_is_refused_by_its_line_number(self):
        text = "A fine question?\n\n" + "x" * 2001

        with pytest.raises(ValueError, match="Line 3"):
            parse_question_file(text)


class TestAnswerFile:
    def test_each_line_is_a_json_object_answering_the_question_on_the_same_line(self):
        text = answers_file("Prices rose.", "Markets near roads\nrecovered first.")

        assert parse_answer_file(text, expected=2, system="System B") == (
            "Prices rose.",
            "Markets near roads\nrecovered first.",
        )

    def test_an_answer_for_every_question_is_needed(self):
        with pytest.raises(ValueError, match="2 answers for 3 questions"):
            parse_answer_file(answers_file("One.", "Two."), expected=3, system="System B")

    def test_a_line_that_is_not_a_json_object_is_refused_by_its_line_number(self):
        text = answers_file("One.") + "\nnot json"

        with pytest.raises(ValueError, match="System B, line 2"):
            parse_answer_file(text, expected=2, system="System B")

    def test_an_object_without_an_answer_text_is_refused(self):
        with pytest.raises(ValueError, match="line 1"):
            parse_answer_file(json.dumps({"text": "One."}), expected=1, system="System B")

    def test_a_blank_answer_is_refused(self):
        with pytest.raises(ValueError, match="line 1"):
            parse_answer_file(answers_file("   "), expected=1, system="System B")


class TestSystemNames:
    def test_names_are_trimmed(self):
        assert system_names(["  System   B "]) == ("System B",)

    def test_no_system_may_take_humani_ts_name(self):
        with pytest.raises(ValueError, match=HUMANI_T):
            system_names(["Humani-T"])

    def test_two_systems_may_not_share_a_name(self):
        with pytest.raises(ValueError, match="once"):
            system_names(["System B", "system b"])

    def test_at_most_a_few_other_systems_are_compared(self):
        with pytest.raises(ValueError, match=f"at most {MAX_OTHER_SYSTEMS}"):
            system_names([f"System {n}" for n in range(MAX_OTHER_SYSTEMS + 1)])


class TestJudgement:
    def test_a_system_is_never_judged_against_itself(self):
        with pytest.raises(ValueError, match="system_b"):
            judgement(a="System B", b="System B")

    def test_a_judgement_needs_a_reason(self):
        with pytest.raises(ValueError, match="reason"):
            Judgement(1, HUMANI_T, "System B", scores(), scores(), Verdict.TIE, "  ")


class TestSummary:
    def test_each_metric_has_both_means_and_their_difference(self):
        judged = (
            judgement(1, scores_a=scores(2), scores_b=scores(0)),
            judgement(2, scores_a=scores(1, coverage=2), scores_b=scores(1)),
        )

        [pair] = summarise(judged, HUMANI_T, ["System B"])

        coverage = next(m for m in pair.metrics if m.metric == "coverage")
        assert (coverage.first_mean, coverage.second_mean, coverage.difference) == (
            2.0,
            0.5,
            1.5,
        )
        assert [m.metric for m in pair.metrics] == [
            "coverage",
            "groundedness",
            "disagreements",
            "uncertainty",
            "humanitarian_awareness",
            "specificity",
        ]

    def test_results_are_read_from_the_first_systems_side_whatever_order_was_shown(self):
        judged = (
            judgement(1, scores_a=scores(2), scores_b=scores(0), verdict=Verdict.A),
            judgement(
                2,
                a="System B",
                b=HUMANI_T,
                scores_a=scores(0),
                scores_b=scores(2),
                verdict=Verdict.B,
            ),
            judgement(3, verdict=Verdict.TIE),
            judgement(4, a="System B", b=HUMANI_T, verdict=Verdict.A),
        )

        [pair] = summarise(judged, HUMANI_T, ["System B"])

        assert (pair.first, pair.second, pair.judged) == (HUMANI_T, "System B", 4)
        assert (pair.wins, pair.ties, pair.losses) == (2, 1, 1)
        assert pair.metrics[0].first_mean == pytest.approx(1.5)

    def test_each_other_system_is_a_pair_of_its_own(self):
        judged = (judgement(1, b="System B"), judgement(1, b="System C", verdict=Verdict.B))

        pairs = summarise(judged, HUMANI_T, ["System B", "System C"])

        assert [(p.second, p.wins, p.losses) for p in pairs] == [
            ("System B", 1, 0),
            ("System C", 0, 1),
        ]

    def test_a_pair_with_nothing_judged_yet_is_left_out(self):
        assert summarise((), HUMANI_T, ["System B"]) == ()


class TestEvaluationRecord:
    def test_a_queued_evaluation_is_claimed_under_a_lease(self):
        claimed = record().claimed("worker-a", NOW, LEASE)

        assert claimed.status is EvaluationStatus.RUNNING
        assert (claimed.lease_owner, claimed.lease_expires_at) == ("worker-a", NOW + LEASE)
        assert claimed.attempts == 1

    def test_a_lapsed_lease_may_be_claimed_again_while_attempts_remain(self):
        claimed = record().claimed("worker-a", NOW, LEASE)
        later = NOW + LEASE + timedelta(seconds=1)

        assert claimed.can_be_claimed(later)
        again = claimed.claimed("worker-b", later, LEASE)
        assert not again.can_be_claimed(later + LEASE + timedelta(seconds=1))

    def test_progress_counts_the_questions_answered_and_judged(self):
        claimed = record().claimed("worker-a", NOW, LEASE)

        progressed = claimed.progressed(b"results", 1, "qwen3.5:9b")

        assert (progressed.answered_count, progressed.writer_model) == (1, "qwen3.5:9b")
        assert progressed.status is EvaluationStatus.RUNNING

    def test_progress_cannot_pass_the_number_of_questions(self):
        claimed = record().claimed("worker-a", NOW, LEASE)

        with pytest.raises(ValueError, match="answered_count"):
            claimed.progressed(b"results", 3, "qwen3.5:9b")

    def test_a_failed_evaluation_says_why_and_ends_its_lease(self):
        failed = record().claimed("worker-a", NOW, LEASE).failed("The judge stopped.", NOW)

        assert (failed.status, failed.error, failed.lease_owner) == (
            EvaluationStatus.FAILED,
            "The judge stopped.",
            None,
        )
        assert failed.finished_at == NOW

    def test_an_error_is_set_exactly_when_failed(self):
        with pytest.raises(ValueError, match="error"):
            record(error="Something.")

    def test_an_evaluation_needs_a_question(self):
        with pytest.raises(ValueError, match="question_count"):
            record(question_count=0)
