"""Requirements for reviews, runs, run events and gates, as pure domain rules.

The transition tables below are the state machines: changing what may follow what is a
visible diff here.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.projects import Action
from app.domain.reviews import (
    MAX_QUESTION_LENGTH,
    InvalidTransitionError,
    Review,
    ReviewStatus,
    ReviewTrigger,
    next_review_status,
    normalize_question,
)
from app.domain.runs import (
    MAX_RECOVERIES,
    STAGE_LABELS,
    STAGES,
    EventName,
    Gate,
    GateDecision,
    GateKind,
    Run,
    RunEvent,
    RunState,
    RunTrigger,
    action_to_resolve,
    agent_step_payload,
    job_finished_payload,
    next_run_state,
    plan_stages,
    recovery_for,
    resume_point,
)

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)

S = ReviewStatus
T = ReviewTrigger

# A run may start from any state in which no run is going: before acceptance its
# first stage asks for the vocabulary through a `vocabulary` gate, and after a run
# has ended it may run again.
REVIEW_TRANSITIONS: dict[tuple[ReviewStatus, ReviewTrigger], ReviewStatus] = {
    (S.NEW, T.CLARIFY): S.CLARIFYING,
    (S.NEW, T.DRAFT): S.DRAFTING,
    (S.CLARIFYING, T.DRAFT): S.DRAFTING,
    (S.CHANGES_REQUESTED, T.DRAFT): S.DRAFTING,
    (S.DRAFTING, T.REQUEST_CHANGES): S.CHANGES_REQUESTED,
    # Editing an accepted plan makes its next version.
    (S.ACCEPTED, T.REQUEST_CHANGES): S.CHANGES_REQUESTED,
    (S.DONE, T.REQUEST_CHANGES): S.CHANGES_REQUESTED,
    (S.FAILED, T.REQUEST_CHANGES): S.CHANGES_REQUESTED,
    (S.CANCELLED, T.REQUEST_CHANGES): S.CHANGES_REQUESTED,
    (S.DRAFTING, T.ACCEPT): S.ACCEPTED,
    (S.NEW, T.START_RUN): S.RUNNING,
    (S.CLARIFYING, T.START_RUN): S.RUNNING,
    (S.DRAFTING, T.START_RUN): S.RUNNING,
    (S.CHANGES_REQUESTED, T.START_RUN): S.RUNNING,
    (S.ACCEPTED, T.START_RUN): S.RUNNING,
    (S.DONE, T.START_RUN): S.RUNNING,
    (S.FAILED, T.START_RUN): S.RUNNING,
    (S.CANCELLED, T.START_RUN): S.RUNNING,
    (S.RUNNING, T.PAUSE): S.WAITING,
    (S.WAITING, T.RESUME): S.RUNNING,
    (S.RUNNING, T.FINISH): S.DONE,
    (S.RUNNING, T.FAIL): S.FAILED,
    (S.RUNNING, T.CANCEL): S.CANCELLED,
    (S.WAITING, T.CANCEL): S.CANCELLED,
}

R = RunState
RT = RunTrigger

RUN_TRANSITIONS: dict[tuple[RunState, RunTrigger], RunState] = {
    (R.QUEUED, RT.CLAIM): R.RUNNING,
    (R.RUNNING, RT.PAUSE): R.WAITING,
    (R.WAITING, RT.RESUME): R.QUEUED,
    # A lease that expired (the worker crashed) or was given back at shutdown.
    (R.RUNNING, RT.REQUEUE): R.QUEUED,
    (R.RUNNING, RT.FINISH): R.DONE,
    (R.RUNNING, RT.FAIL): R.FAILED,
    (R.QUEUED, RT.CANCEL): R.CANCELLED,
    (R.RUNNING, RT.CANCEL): R.CANCELLED,
    (R.WAITING, RT.CANCEL): R.CANCELLED,
}

_EVERY_REVIEW_PAIR = [(s, t) for s in ReviewStatus for t in ReviewTrigger]
_EVERY_RUN_PAIR = [(s, t) for s in RunState for t in RunTrigger]


class TestReviewStateMachine:
    def test_the_statuses_are_the_ones_the_spec_names(self):
        assert {s.value for s in ReviewStatus} == {
            "new",
            "clarifying",
            "drafting",
            "changes_requested",
            "accepted",
            "running",
            "waiting",
            "done",
            "failed",
            "cancelled",
        }

    @pytest.mark.parametrize(
        ("status", "trigger"),
        _EVERY_REVIEW_PAIR,
        ids=[f"{s.value}-{t.value}" for s, t in _EVERY_REVIEW_PAIR],
    )
    def test_each_transition_is_exactly_what_the_table_allows(self, status, trigger):
        expected = REVIEW_TRANSITIONS.get((status, trigger))
        if expected is None:
            with pytest.raises(InvalidTransitionError):
                next_review_status(status, trigger)
        else:
            assert next_review_status(status, trigger) is expected

    def test_a_second_run_cannot_start_while_one_is_going(self):
        for status in (S.RUNNING, S.WAITING):
            with pytest.raises(InvalidTransitionError):
                next_review_status(status, T.START_RUN)


class TestRunStateMachine:
    @pytest.mark.parametrize(
        ("state", "trigger"),
        _EVERY_RUN_PAIR,
        ids=[f"{s.value}-{t.value}" for s, t in _EVERY_RUN_PAIR],
    )
    def test_each_transition_is_exactly_what_the_table_allows(self, state, trigger):
        expected = RUN_TRANSITIONS.get((state, trigger))
        if expected is None:
            with pytest.raises(InvalidTransitionError):
                next_run_state(state, trigger)
        else:
            assert next_run_state(state, trigger) is expected

    @pytest.mark.parametrize("state", [R.DONE, R.FAILED, R.CANCELLED], ids=lambda s: s.value)
    def test_a_finished_run_never_changes_again(self, state):
        for trigger in RunTrigger:
            with pytest.raises(InvalidTransitionError):
                next_run_state(state, trigger)


class TestQuestion:
    def test_surrounding_whitespace_is_trimmed(self):
        assert normalize_question("  Do cash transfers help?\n") == "Do cash transfers help?"

    def test_line_breaks_inside_a_question_are_kept(self):
        assert normalize_question("Context: Gao.\nQuestion: cash or vouchers?") == (
            "Context: Gao.\nQuestion: cash or vouchers?"
        )

    @pytest.mark.parametrize("question", ["", "   ", "\n\t"])
    def test_a_blank_question_is_refused(self, question):
        with pytest.raises(ValueError, match=r"Review\.question"):
            normalize_question(question)

    def test_a_question_at_the_limit_is_accepted_and_one_over_is_refused(self):
        assert len(normalize_question("x" * MAX_QUESTION_LENGTH)) == MAX_QUESTION_LENGTH
        with pytest.raises(ValueError, match=r"Review\.question"):
            normalize_question("x" * (MAX_QUESTION_LENGTH + 1))

    @pytest.mark.parametrize("question", ["Gao\x00?", "cash‮vouchers"])
    def test_control_and_direction_override_characters_are_refused(self, question):
        with pytest.raises(ValueError, match=r"Review\.question"):
            normalize_question(question)


def _review(**overrides: object) -> Review:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "question_sealed": b"sealed-question",
        "status": ReviewStatus.NEW,
        "created_at": NOW,
        "created_by": uuid.uuid4(),
    }
    values.update(overrides)
    return Review(**values)  # type: ignore[arg-type]


class TestReview:
    def test_the_question_is_held_only_as_ciphertext(self):
        assert not hasattr(_review(), "question")

    def test_an_empty_ciphertext_is_refused(self):
        with pytest.raises(ValueError, match="question_sealed"):
            _review(question_sealed=b"")

    def test_timestamps_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _review(created_at=datetime(2026, 11, 3, 9, 0))  # noqa: DTZ001 — the invalid input under test

    def test_a_transition_changes_only_the_status(self):
        review = _review()

        started = review.transitioned(T.START_RUN)

        assert started.status is S.RUNNING
        assert replace(started, status=S.NEW) == review


class TestStagePlan:
    def test_the_stages_follow_the_prototypes_pipeline_in_order(self):
        assert STAGES == (
            "queries",
            "discover",
            "ingest",
            "mask",
            "screen",
            "export",
            "answer",
        )

    def test_every_stage_has_a_plain_name_for_the_interface(self):
        assert set(STAGE_LABELS) == set(STAGES)
        assert STAGE_LABELS["answer"] == "Brief"

    def test_requested_stages_run_in_pipeline_order_whatever_order_they_were_given_in(self):
        assert plan_stages(["screen", "queries", "discover"], STAGES) == (
            "queries",
            "discover",
            "screen",
        )

    def test_no_stages_means_the_whole_pipeline(self):
        assert plan_stages([], STAGES) == STAGES

    def test_the_whole_pipeline_needs_every_stage_to_be_available(self):
        with pytest.raises(ValueError, match="cannot run"):
            plan_stages([], ("queries", "discover"))

    def test_an_unknown_stage_is_refused(self):
        with pytest.raises(ValueError, match="stages"):
            plan_stages(["queries", "summon"], STAGES)

    def test_a_stage_asked_for_twice_is_refused(self):
        with pytest.raises(ValueError, match="stages"):
            plan_stages(["queries", "queries"], STAGES)

    def test_a_stage_this_deployment_cannot_run_is_refused(self):
        with pytest.raises(ValueError, match="stages"):
            plan_stages(["queries", "answer"], ("queries", "discover"))

    def test_the_retired_index_stage_cannot_be_asked_for(self):
        with pytest.raises(ValueError, match="unknown"):
            plan_stages(["export", "index"], STAGES)


def _run(**overrides: object) -> Run:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "review_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "stages": ("queries", "discover", "screen"),
        "state": RunState.QUEUED,
        "created_at": NOW,
        "created_by": uuid.uuid4(),
        "queued_at": NOW,
    }
    values.update(overrides)
    return Run(**values)  # type: ignore[arg-type]


LEASE = timedelta(seconds=60)


class TestRun:
    def test_a_new_run_starts_at_its_first_stage(self):
        run = _run()

        assert run.current_stage == "queries"
        assert run.position == 1
        assert run.total == 3
        assert not run.is_finished

    def test_claiming_takes_a_lease_and_records_the_first_start(self):
        claimed = _run().claimed("worker-a", NOW, LEASE)

        assert claimed.state is R.RUNNING
        assert claimed.lease_owner == "worker-a"
        assert claimed.lease_expires_at == NOW + LEASE
        assert claimed.started_at == NOW

    def test_a_second_claim_keeps_the_first_start_time(self):
        later = NOW + timedelta(minutes=5)
        run = _run().claimed("worker-a", NOW, LEASE).paused().resumed(later)

        assert run.claimed("worker-b", later, LEASE).started_at == NOW

    def test_finishing_a_stage_moves_to_the_next_and_forgets_past_crashes(self):
        run = replace(_run().claimed("w", NOW, LEASE).stage_started(NOW), recoveries=1)

        finished = run.stage_finished()

        assert finished.stages_finished == 1
        assert finished.current_stage == "discover"
        assert finished.position == 2
        assert finished.stage_started_at is None
        assert finished.recoveries == 0

    def test_pausing_gives_the_lease_back(self):
        paused = _run().claimed("w", NOW, LEASE).paused()

        assert paused.state is R.WAITING
        assert paused.lease_owner is None
        assert paused.lease_expires_at is None

    def test_finishing_records_when_and_gives_the_lease_back(self):
        done = _run().claimed("w", NOW, LEASE).finished(RT.FINISH, NOW)

        assert done.state is R.DONE
        assert done.is_finished
        assert done.finished_at == NOW
        assert done.lease_owner is None

    def test_only_a_finishing_trigger_can_finish_a_run(self):
        with pytest.raises(ValueError, match="trigger"):
            _run().claimed("w", NOW, LEASE).finished(RT.PAUSE, NOW)

    def test_a_cancel_request_on_a_running_run_is_recorded_without_stopping_it(self):
        run = _run().claimed("w", NOW, LEASE).with_cancel_requested(NOW)

        assert run.state is R.RUNNING
        assert run.cancel_requested_at == NOW

    def test_asking_twice_to_cancel_keeps_the_first_request(self):
        run = _run().claimed("w", NOW, LEASE).with_cancel_requested(NOW)

        assert run.with_cancel_requested(NOW + LEASE).cancel_requested_at == NOW

    def test_a_lease_has_expired_only_strictly_after_its_end(self):
        run = _run().claimed("w", NOW, LEASE)

        assert not run.has_expired_lease(NOW + LEASE)
        assert run.has_expired_lease(NOW + LEASE + timedelta(seconds=1))

    def test_a_run_that_is_not_running_has_no_lease_to_expire(self):
        assert not _run().has_expired_lease(NOW + timedelta(days=1))

    def test_elapsed_seconds_count_from_the_first_start(self):
        run = _run().claimed("w", NOW, LEASE)

        assert run.seconds(NOW + timedelta(seconds=90)) == 90.0

    def test_a_run_never_claimed_has_run_for_no_time(self):
        assert _run().seconds(NOW + timedelta(hours=1)) == 0.0

    def test_stages_must_be_a_valid_plan(self):
        with pytest.raises(ValueError, match="stages"):
            _run(stages=())

    def test_a_run_stored_with_the_retired_index_stage_still_loads(self):
        run = _run(stages=("export", "index", "answer"))

        assert run.stages == ("export", "index", "answer")

    def test_the_retired_index_stage_keeps_its_place_in_the_order(self):
        with pytest.raises(ValueError, match="order"):
            _run(stages=("index", "export"))

    def test_timestamps_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _run(queued_at=datetime(2026, 11, 3, 9, 0))  # noqa: DTZ001 — the invalid input under test


class TestCrashRecovery:
    """A run whose worker vanished is resumed or failed, never left running forever."""

    def test_an_expired_run_is_resumed_while_it_has_recoveries_left(self):
        run = _run().claimed("w", NOW, LEASE)

        assert recovery_for(run) is RT.REQUEUE

    def test_an_expired_run_that_keeps_crashing_is_failed(self):
        run = replace(_run().claimed("w", NOW, LEASE), recoveries=MAX_RECOVERIES)

        assert recovery_for(run) is RT.FAIL

    def test_an_expired_run_someone_asked_to_cancel_is_cancelled(self):
        run = _run().claimed("w", NOW, LEASE).with_cancel_requested(NOW)

        assert recovery_for(run) is RT.CANCEL

    def test_requeueing_counts_the_recovery_and_keeps_the_stage_started(self):
        run = _run().claimed("w", NOW, LEASE).stage_started(NOW)

        requeued = run.requeued(NOW + LEASE)

        assert requeued.state is R.QUEUED
        assert requeued.recoveries == 1
        assert requeued.stage_started_at == NOW
        assert requeued.lease_owner is None


class TestEvents:
    def test_the_event_names_are_the_ones_the_contract_names(self):
        assert {e.value for e in EventName} == {
            "stage.started",
            "stage.progress",
            "stage.finished",
            "agent.step",
            "gate.required",
            "gate.resolved",
            "job.finished",
        }

    def test_event_ids_start_at_one(self):
        with pytest.raises(ValueError, match=r"RunEvent\.id"):
            RunEvent(
                run_id=uuid.uuid4(),
                id=0,
                name=EventName.STAGE_STARTED,
                payload_sealed=b"x",
                occurred_at=NOW,
            )

    def test_an_event_payload_is_held_only_as_ciphertext(self):
        event = RunEvent(
            run_id=uuid.uuid4(),
            id=1,
            name=EventName.STAGE_STARTED,
            payload_sealed=b"x",
            occurred_at=NOW,
        )

        assert not hasattr(event, "payload")


# The WHATWG "Server-sent events" reconnect: the browser sends the last id it saw.
# An id the server does not recognise replays from the start.
RESUME_POINTS = [
    (None, 0),
    ("3", 3),
    (" 3 ", 3),
    ("9", 9),
    ("0", 0),
    ("10", 0),
    ("-1", 0),
    ("2.5", 0),
    ("abc", 0),
    ("", 0),
    ("٣", 0),
    ("99999999999999999999", 0),
]


class TestResumePoint:
    @pytest.mark.parametrize(("header", "expected"), RESUME_POINTS)
    def test_a_reconnect_resumes_after_an_id_this_run_has_or_else_from_the_start(
        self, header, expected
    ):
        assert resume_point(header, last_id=9) == expected


class TestEventPayloads:
    def test_an_agent_step_needs_its_round_role_and_status(self):
        step = {"round": 1, "role": "writer", "status": "ok", "tokens": 812}

        assert agent_step_payload(step) == step

    @pytest.mark.parametrize(
        "step",
        [
            {"role": "writer", "status": "ok"},
            {"round": -1, "role": "writer", "status": "ok"},
            {"round": True, "role": "writer", "status": "ok"},
            {"round": 1, "role": "oracle", "status": "ok"},
            {"round": 1, "role": "writer", "status": "fine"},
        ],
        ids=["no-round", "negative-round", "boolean-round", "unknown-role", "unknown-status"],
    )
    def test_an_agent_step_without_them_is_refused(self, step):
        with pytest.raises(ValueError, match=r"agent\.step"):
            agent_step_payload(step)

    def test_only_a_finished_state_can_be_an_outcome(self):
        with pytest.raises(ValueError, match="outcome"):
            job_finished_payload(RunState.RUNNING, 0.0, None)


# Approving masking is for owners ("Only owners can do this"),
# because checking it means reading raw text; a vocabulary is any contributor's.
GATE_ACTIONS = {
    GateKind.VOCABULARY: Action.CONFIRM_GATE,
    GateKind.PRIVACY_REVIEW: Action.MANAGE_DOCUMENTS,
}


def _gate(**overrides: object) -> Gate:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "stage": "mask",
        "kind": GateKind.PRIVACY_REVIEW,
        "payload_sealed": b"sealed",
        "created_at": NOW,
    }
    values.update(overrides)
    return Gate(**values)  # type: ignore[arg-type]


class TestGates:
    def test_there_are_exactly_two_gate_kinds(self):
        assert {k.value for k in GateKind} == {"vocabulary", "privacy_review"}

    def test_a_decision_approves_or_rejects(self):
        assert {d.value for d in GateDecision} == {"approve", "reject"}

    @pytest.mark.parametrize("kind", list(GateKind), ids=lambda k: k.value)
    def test_each_gate_kind_needs_the_action_the_table_names(self, kind):
        assert action_to_resolve(kind) is GATE_ACTIONS[kind]

    def test_a_new_gate_is_open(self):
        gate = _gate()

        assert not gate.is_resolved
        assert gate.decision is None

    def test_resolving_records_who_what_and_when(self):
        by = uuid.uuid4()

        resolved = _gate().resolved(by, GateDecision.APPROVE, None, NOW)

        assert resolved.is_resolved
        assert (resolved.resolved_by, resolved.decision, resolved.resolved_at) == (
            by,
            GateDecision.APPROVE,
            NOW,
        )

    def test_a_stage_must_be_a_known_stage(self):
        with pytest.raises(ValueError, match=r"Gate\.stage"):
            _gate(stage="summon")

    def test_a_half_resolved_gate_is_refused(self):
        with pytest.raises(ValueError, match="resolved"):
            _gate(resolved_at=NOW)
