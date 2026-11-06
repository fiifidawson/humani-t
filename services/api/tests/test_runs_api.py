"""Requirements for reviews, runs, the live event stream and gates, over HTTP.

Sources: architecture §4.4 (the contract: routes, event names and fields, gate
rules, `Last-Event-ID` replay, access), §5.4 (encryption, crypto-shredding),
§5.5 (404 for non-members, audit with ids only); UI_UX_SPEC §11.1, §11.2,
§5.3.5, §5.3.6 and §6.7; the WHATWG HTML standard, "Server-sent events"
(wire format, `retry`, `Last-Event-ID`); OWASP ASVS 5.0 V8 and V16.

The rules, in the order the API applies them, are the projects' own
(`test_projects_api.py`): 401, then 404 for a non-member with the same body as
for a missing id, then 403 for a role that may not, then the request itself.
A run, gate or event route does not name its project, so the project is
resolved from the review, run or gate first.
"""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import pytest

from app.api.schemas.run_events import RunStreamEvent
from app.domain.encryption import DecryptionError, KeyDestroyedError
from app.domain.runs import STAGES, GateDecision, GateKind, RunState
from app.runs.fake import FakeStage
from tests.project_support import (
    OWNER_EMAIL,
    PROJECTS,
    REVIEWER_EMAIL,
    audit_events,
    send,
    signed_in,
)
from tests.run_support import (
    QUESTION,
    FailingStage,
    HookedStage,
    RunTeam,
    create_review,
    current_of,
    event_context,
    failing_with_message,
    make_run_team,
    names,
    parse_sse,
    reviews_path,
    run_until_idle,
    simple_stages,
)

REVIEW_FIELDS = {"id", "project_id", "question", "status", "created_at"}
RUN_FIELDS = {
    "id",
    "review_id",
    "stages",
    "state",
    "outcome",
    "stages_finished",
    "cancel_requested",
    "created_at",
    "started_at",
    "finished_at",
}
MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def _gated(kind: GateKind = GateKind.PRIVACY_REVIEW):
    return {
        "queries": FakeStage(items=1),
        "discover": FakeStage(items=2, gate=kind),
    }


class TestReviews:
    @pytest.mark.parametrize("role", ["owner", "reviewer"])
    def test_owners_and_reviewers_create_a_review_with_a_question(self, rt, role):
        browser = rt.team.browser_for(role)

        response = send(browser, "POST", reviews_path(rt.project_id), {"question": " Cash? "})

        assert response.status_code == 201
        body = response.json()
        assert set(body) >= REVIEW_FIELDS
        assert body["question"] == "Cash?"
        assert body["status"] == "new"
        assert body["project_id"] == rt.project_id
        assert response.headers["location"] == reviews_path(rt.project_id, f"/{body['id']}")

    def test_a_viewer_may_not_create_a_review(self, rt):
        response = send(rt.viewer, "POST", reviews_path(rt.project_id), {"question": "Cash?"})

        assert response.status_code == 403

    @pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
    def test_an_unusable_question_is_refused(self, rt, question):
        response = send(rt.owner, "POST", reviews_path(rt.project_id), {"question": question})

        assert response.status_code == 422

    def test_fields_the_client_may_not_set_are_refused(self, rt):
        response = send(
            rt.owner,
            "POST",
            reviews_path(rt.project_id),
            {"question": "Cash?", "status": "accepted"},
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_lists_and_reads_the_projects_reviews(self, rt, role):
        browser = rt.team.browser_for(role)

        listed = browser.get(reviews_path(rt.project_id))
        read = browser.get(reviews_path(rt.project_id, f"/{rt.review_id}"))

        assert listed.status_code == read.status_code == 200
        assert [r["id"] for r in listed.json()] == [rt.review_id]
        assert read.json()["question"] == QUESTION

    def test_a_review_is_not_found_through_another_projects_path(self, rt):
        other = send(rt.owner, "POST", PROJECTS, {"name": "Market assessment, Mopti"}).json()

        response = rt.owner.get(reviews_path(other["id"], f"/{rt.review_id}"))

        assert response.status_code == 404

    def test_the_question_is_never_stored_in_the_clear(self, rt):
        """Brief rule 15: project content is encrypted at rest under the project's key."""
        assert "malnutrition" not in repr(vars(rt.harness.storage.state))

    def test_creating_a_review_is_audited_with_ids_only(self, rt):
        created = [e for e in audit_events(rt.harness) if e.entry.action == "review.created"]

        assert [(e.entry.target_type, e.entry.target_id) for e in created] == [
            ("review", rt.review_id)
        ]
        assert created[0].entry.scope_id == UUID(rt.project_id)


class TestReviewAuthor:
    """UI_UX_SPEC §5.2 and §5.3.1: a question card shows who asked it, by display name."""

    def test_a_review_names_its_author_by_id_and_display_name(self, rt):
        body = rt.owner.get(reviews_path(rt.project_id, f"/{rt.review_id}")).json()

        assert body["created_by"] == {
            "id": rt.team.user_id(OWNER_EMAIL),
            "display_name": OWNER_EMAIL.split("@")[0],
        }

    def test_every_member_sees_the_author_in_the_list_and_on_create(self, rt):
        created = send(rt.reviewer, "POST", reviews_path(rt.project_id), {"question": "Q?"})

        listed = rt.viewer.get(reviews_path(rt.project_id)).json()

        reviewer = {"id": rt.team.user_id(REVIEWER_EMAIL), "display_name": "kofi"}
        assert created.json()["created_by"] == reviewer
        # Found by id: the test clock gives both reviews one timestamp, so the
        # newest-first order falls back to their random ids.
        (new,) = (review for review in listed if review["id"] == created.json()["id"])
        assert new["created_by"] == reviewer

    def test_a_non_member_sees_no_author_at_all(self, rt):
        response = rt.outsider.get(reviews_path(rt.project_id, f"/{rt.review_id}"))

        assert response.status_code == 404
        assert "amara" not in response.text


class TestStartingARun:
    @pytest.mark.parametrize("role", ["owner", "reviewer"])
    def test_owners_and_reviewers_start_a_run_and_get_202_at_once(self, rt, role):
        response = send(
            rt.team.browser_for(role),
            "POST",
            f"/api/v1/reviews/{rt.review_id}/runs",
            {"stages": ["discover", "queries"]},
        )

        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert response.headers["location"] == f"/api/v1/runs/{run_id}/events"
        run = rt.stored_run(run_id)
        assert run.state is RunState.QUEUED
        assert run.stages == ("queries", "discover")

    def test_a_viewer_may_not_start_a_run(self, rt):
        response = send(
            rt.viewer, "POST", f"/api/v1/reviews/{rt.review_id}/runs", {"stages": ["queries"]}
        )

        assert response.status_code == 403

    @pytest.mark.parametrize("stages", [["summon"], ["queries", "queries"]])
    def test_an_unusable_stage_list_is_refused(self, rt, stages):
        response = send(
            rt.owner, "POST", f"/api/v1/reviews/{rt.review_id}/runs", {"stages": stages}
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("body", [{"stages": []}, {}], ids=["empty", "omitted"])
    def test_no_stages_means_the_whole_pipeline_in_order(self, rt, body):
        response = send(rt.owner, "POST", f"/api/v1/reviews/{rt.review_id}/runs", body)

        assert response.status_code == 202
        assert rt.stored_run(response.json()["run_id"]).stages == STAGES

    def test_a_second_run_cannot_start_while_one_is_going(self, rt):
        rt.start_run()

        response = send(
            rt.owner, "POST", f"/api/v1/reviews/{rt.review_id}/runs", {"stages": ["queries"]}
        )

        assert response.status_code == 409

    def test_a_review_runs_again_once_its_run_has_finished(self, rt):
        rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        assert rt.start_run(["queries"])

    def test_the_review_shows_its_run_state(self, rt):
        rt.start_run(["queries"])
        running = rt.owner.get(reviews_path(rt.project_id, f"/{rt.review_id}")).json()
        run_until_idle(rt.worker(simple_stages("queries")))
        done = rt.owner.get(reviews_path(rt.project_id, f"/{rt.review_id}")).json()

        assert (running["status"], done["status"]) == ("running", "done")

    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_lists_a_reviews_runs_newest_first(self, rt, role):
        first = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))
        rt.clock.advance(minutes=1)
        second = rt.start_run(["queries"])

        response = rt.team.browser_for(role).get(f"/api/v1/reviews/{rt.review_id}/runs")

        assert response.status_code == 200
        body = response.json()
        assert [r["id"] for r in body] == [second, first]
        assert set(body[0]) >= RUN_FIELDS
        assert (body[1]["state"], body[1]["outcome"], body[1]["stages_finished"]) == (
            "done",
            "done",
            1,
        )

    def test_starting_a_run_is_audited_with_ids_only(self, rt):
        run_id = rt.start_run()

        started = [e for e in audit_events(rt.harness) if e.entry.action == "run.started"]

        assert [(e.entry.target_type, e.entry.target_id) for e in started] == [("run", run_id)]
        assert started[0].entry.scope_id == UUID(rt.project_id)
        assert started[0].entry.actor_id == UUID(rt.team.user_id("amara@example.org"))


class TestTheEventStream:
    def _finished_run(self, rt: RunTeam) -> str:
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(simple_stages("queries", "discover", items=2)))
        return run_id

    def test_the_stream_is_plain_server_sent_events(self, rt):
        response = rt.stream(self._finished_run(rt))

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"

    def test_each_event_has_an_id_a_name_and_one_line_of_json(self, rt):
        body = rt.stream(self._finished_run(rt)).text

        blocks = [b for b in body.split("\n\n") if b.startswith("id:")]
        assert blocks
        for block in blocks:
            lines = block.split("\n")
            assert [line.split(":", 1)[0] for line in lines] == ["id", "event", "data"]

    def test_the_stream_sets_the_browsers_reconnect_delay_first(self, rt):
        body = rt.stream(self._finished_run(rt)).text

        retries, _, _ = parse_sse(body)
        assert body.startswith("retry: ")
        assert retries == ["3000"]

    def test_a_run_reports_its_stages_in_order_and_finishes_exactly_once(self, rt):
        _, events, _ = parse_sse(rt.stream(self._finished_run(rt)).text)

        assert names(events) == [
            "stage.started",
            "stage.progress",
            "stage.progress",
            "stage.finished",
            "stage.started",
            "stage.progress",
            "stage.progress",
            "stage.finished",
            "job.finished",
        ]
        assert [e.id for e in events] == list(range(1, len(events) + 1))

    def test_each_event_carries_the_fields_the_contract_names(self, rt):
        _, events, _ = parse_sse(rt.stream(self._finished_run(rt)).text)
        by_name = {e.name: e.data for e in events}

        assert set(by_name["stage.started"]) == {"stage", "position", "total", "at"}
        assert (by_name["stage.started"]["stage"], by_name["stage.started"]["total"]) == (
            "discover",
            2,
        )
        assert set(by_name["stage.progress"]) == {"stage", "done", "total", "item", "at"}
        assert set(by_name["stage.finished"]) == {"stage", "seconds", "summary"}
        assert by_name["stage.finished"]["summary"] == {"items": 2}
        assert by_name["job.finished"] == {"outcome": "done", "seconds": 0.0, "error": None}

    def test_stages_are_numbered_within_this_run(self, rt):
        _, events, _ = parse_sse(rt.stream(self._finished_run(rt)).text)

        started = [e.data for e in events if e.name == "stage.started"]
        assert [(s["stage"], s["position"], s["total"]) for s in started] == [
            ("queries", 1, 2),
            ("discover", 2, 2),
        ]

    @pytest.mark.parametrize("seen", [1, 4, 8])
    def test_a_reconnect_replays_every_event_after_the_last_one_seen(self, rt, seen):
        run_id = self._finished_run(rt)
        _, everything, _ = parse_sse(rt.stream(run_id).text)

        _, replayed, _ = parse_sse(rt.stream(run_id, last_event_id=str(seen)).text)

        assert [e.id for e in replayed] == [e.id for e in everything[seen:]]
        assert replayed[-1].name == "job.finished"

    def test_a_reconnect_after_the_last_event_of_a_finished_run_replays_nothing(self, rt):
        run_id = self._finished_run(rt)
        _, everything, _ = parse_sse(rt.stream(run_id).text)

        response = rt.stream(run_id, last_event_id=str(everything[-1].id))

        _, replayed, _ = parse_sse(response.text)
        assert response.status_code == 200
        assert replayed == []

    @pytest.mark.parametrize(
        "unknown", ["abc", "-1", "0", "2.5", "999", "99999999999999999999"]
    )
    def test_an_id_the_server_does_not_recognise_replays_from_the_start(self, rt, unknown):
        run_id = self._finished_run(rt)
        _, everything, _ = parse_sse(rt.stream(run_id).text)

        _, replayed, _ = parse_sse(rt.stream(run_id, last_event_id=unknown).text)

        assert [e.id for e in replayed] == [e.id for e in everything]

    def test_a_new_event_source_resumes_after_the_id_in_the_query(self, rt):
        """A replacement `EventSource` cannot set the header; the query carries the id."""
        run_id = self._finished_run(rt)

        response = rt.owner.get(f"/api/v1/runs/{run_id}/events?last_event_id=7")

        _, replayed, _ = parse_sse(response.text)
        assert [e.id for e in replayed] == [8, 9]

    @pytest.mark.parametrize("unknown", ["abc", "0", "-1", "999"])
    def test_an_unknown_id_in_the_query_replays_from_the_start(self, rt, unknown):
        run_id = self._finished_run(rt)

        response = rt.owner.get(f"/api/v1/runs/{run_id}/events?last_event_id={unknown}")

        _, replayed, _ = parse_sse(response.text)
        assert [e.id for e in replayed] == list(range(1, 10))

    @pytest.mark.parametrize(
        ("header", "expected"), [("4", [5, 6, 7, 8, 9]), ("abc", list(range(1, 10)))]
    )
    def test_the_header_wins_over_the_query(self, rt, header, expected):
        run_id = self._finished_run(rt)

        _, replayed, _ = parse_sse(
            rt.stream(run_id, last_event_id=header, query="?last_event_id=7").text
        )

        assert [e.id for e in replayed] == expected

    def test_every_event_matches_its_published_schema(self, rt):
        """The web app generates its event types from these schemas; the stream obeys them."""
        run_id = rt.start_run(["queries", "discover", "answer"])
        worker = rt.worker(
            _gated(GateKind.VOCABULARY) | {"answer": FakeStage(items=1, agent_rounds=1)}
        )
        run_until_idle(worker)
        rt.resolve(rt.gate_id(run_id))
        run_until_idle(worker)

        _, events, _ = parse_sse(rt.stream(run_id).text)

        assert {e.name for e in events} == {
            "stage.started",
            "stage.progress",
            "stage.finished",
            "agent.step",
            "gate.required",
            "gate.resolved",
            "job.finished",
        }
        for event in events:
            parsed = RunStreamEvent.model_validate(
                {"id": event.id, "event": event.name, "data": event.data}
            )
            assert parsed.root.event == event.name

    def test_the_event_schemas_are_published_in_openapi(self, rt):
        schema = rt.owner.get("/openapi.json").json()
        components = schema["components"]["schemas"]
        route = schema["paths"]["/api/v1/runs/{run_id}/events"]["get"]

        stream = route["responses"]["200"]["content"]["text/event-stream"]["schema"]
        assert stream == {"$ref": "#/components/schemas/RunStreamEvent"}
        assert set(components) >= {
            "RunStreamEvent",
            "StageStartedEvent",
            "StageProgressEvent",
            "StageFinishedEvent",
            "AgentStepEvent",
            "GateRequiredEvent",
            "GateResolvedEvent",
            "JobFinishedEvent",
        }
        assert components["RunStreamEvent"]["discriminator"]["propertyName"] == "event"
        assert {p["name"] for p in route["parameters"]} >= {"last_event_id", "Last-Event-ID"}

    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_can_follow_a_run(self, rt, role):
        response = rt.stream(self._finished_run(rt), browser=rt.team.browser_for(role))

        assert response.status_code == 200

    def test_a_quiet_stream_sends_keep_alive_comments_and_ends_with_the_session(self, rt):
        """A waiting run sends nothing for hours; proxies must not close it, sessions must."""
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        response = rt.stream(run_id)

        _, events, comments = parse_sse(response.text)
        assert names(events)[-1] == "gate.required"
        assert comments
        assert set(comments) == {"keep-alive"}


class TestGates:
    def test_a_gate_pauses_the_run_and_frees_the_worker(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())

        run_until_idle(worker)

        events = rt.events(run_id)
        assert names(events)[-1] == "gate.required"
        assert set(events[-1].data) == {"gate_id", "kind", "payload"}
        assert events[-1].data["kind"] == "privacy_review"
        assert rt.stored_run(run_id).state is RunState.WAITING
        assert rt.stored_run(run_id).lease_owner is None
        assert asyncio.run(worker.run_once()) is False

    def test_resolving_a_gate_resumes_the_run_with_the_decision(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())
        run_until_idle(worker)
        gate_id = rt.gate_id(run_id)

        response = rt.resolve(gate_id, "approve")
        run_until_idle(worker)

        assert response.status_code == 200
        assert response.json() == {
            "gate_id": gate_id,
            "decision": "approve",
            "by": rt.team.user_id("amara@example.org"),
        }
        events = rt.events(run_id)
        resolved = next(e for e in events if e.name == "gate.resolved")
        assert resolved.data == response.json()
        finished = [e for e in events if e.name == "stage.finished"]
        assert finished[-1].data["summary"] == {"items": 2, "decision": "approve"}
        assert names(events)[-1] == "job.finished"
        assert events[-1].data["outcome"] == "done"

    def test_resuming_does_not_start_the_paused_stage_a_second_time(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        worker = rt.worker(_gated())
        run_until_idle(worker)
        rt.resolve(rt.gate_id(run_id))
        run_until_idle(worker)

        started = [e.data["stage"] for e in rt.events(run_id) if e.name == "stage.started"]
        assert started == ["queries", "discover"]

    def test_the_second_decision_on_a_gate_gets_409(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        gate_id = rt.gate_id(run_id)

        first = rt.resolve(gate_id, "approve")
        second = rt.resolve(
            gate_id, "reject", browser=signed_in(rt.harness, "amara@example.org")
        )

        assert (first.status_code, second.status_code) == (200, 409)
        assert [e.data["decision"] for e in rt.events(run_id) if e.name == "gate.resolved"] == [
            "approve"
        ]

    def test_two_decisions_at_the_same_moment_have_one_winner(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        gate_id = UUID(rt.gate_id(run_id))

        async def race() -> list[object]:
            current = await current_of(rt, rt.owner)
            return await asyncio.gather(
                rt.runs.resolve_gate(current, gate_id, GateDecision.APPROVE, None),
                rt.runs.resolve_gate(current, gate_id, GateDecision.REJECT, None),
                return_exceptions=True,
            )

        outcomes = asyncio.run(race())

        assert sum(not isinstance(o, BaseException) for o in outcomes) == 1
        assert len([e for e in rt.events(run_id) if e.name == "gate.resolved"]) == 1

    def test_only_owners_may_approve_a_privacy_review(self, rt):
        """UI_UX_SPEC §5.3.6: checking masking means reading raw text, which is owner-only."""
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated(GateKind.PRIVACY_REVIEW)))

        response = rt.resolve(rt.gate_id(run_id), browser=rt.reviewer)

        assert response.status_code == 403

    def test_a_reviewer_may_confirm_a_vocabulary(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated(GateKind.VOCABULARY)))

        response = rt.resolve(rt.gate_id(run_id), browser=rt.reviewer)

        assert response.status_code == 200

    def test_a_viewer_may_not_resolve_any_gate(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated(GateKind.VOCABULARY)))

        response = rt.resolve(rt.gate_id(run_id), browser=rt.viewer)

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "body",
        [{}, {"decision": "maybe"}, {"decision": "approve", "revisions": "all"}],
        ids=["missing", "unknown", "revisions-not-an-object"],
    )
    def test_an_unusable_decision_is_refused(self, rt, body):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))

        response = send(rt.owner, "POST", f"/api/v1/gates/{rt.gate_id(run_id)}", body)

        assert response.status_code == 422

    def test_revisions_reach_the_stage_and_are_stored_encrypted(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated(GateKind.VOCABULARY)))

        response = send(
            rt.owner,
            "POST",
            f"/api/v1/gates/{rt.gate_id(run_id)}",
            {"decision": "approve", "revisions": {"terms": ["displaced children in Marib"]}},
        )

        assert response.status_code == 200
        assert "Marib" not in repr(vars(rt.harness.storage.state))

    def test_resolving_a_gate_is_audited_with_ids_only(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        gate_id = rt.gate_id(run_id)

        rt.resolve(gate_id)

        resolved = [e for e in audit_events(rt.harness) if e.entry.action == "gate.resolved"]
        assert [(e.entry.target_type, e.entry.target_id) for e in resolved] == [
            ("gate", gate_id)
        ]
        assert resolved[0].entry.scope_id == UUID(rt.project_id)


class TestCancelling:
    def test_a_queued_run_is_cancelled_at_once(self, rt):
        run_id = rt.start_run()

        response = rt.cancel(run_id)

        assert response.status_code == 202
        assert response.json()["state"] == "cancelled"
        events = rt.events(run_id)
        assert names(events) == ["job.finished"]
        assert events[0].data == {"outcome": "cancelled", "seconds": 0.0, "error": None}

    def test_a_cancelled_run_is_never_picked_up(self, rt):
        run_id = rt.start_run()
        rt.cancel(run_id)

        assert run_until_idle(rt.worker(simple_stages("queries", "discover"))) == 0
        assert rt.stored_run(run_id).state is RunState.CANCELLED

    def test_a_running_run_stops_at_its_next_checkpoint_and_keeps_finished_stages(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        stages = simple_stages("queries")
        stages["discover"] = discover = HookedStage(
            lambda done: rt.cancel(run_id) if done == 1 else None, items=3
        )

        run_until_idle(rt.worker(stages))

        events = rt.events(run_id)
        assert discover.items_done == 1
        assert [e.data["stage"] for e in events if e.name == "stage.finished"] == ["queries"]
        assert names(events)[-1] == "job.finished"
        assert events[-1].data["outcome"] == "cancelled"
        run = rt.stored_run(run_id)
        assert (run.state, run.stages_finished) == (RunState.CANCELLED, 1)

    def test_cancelling_a_running_run_answers_before_the_worker_stops(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        answers = []
        stages = simple_stages("queries")
        stages["discover"] = HookedStage(
            lambda done: answers.append(rt.cancel(run_id).json()) if done == 1 else None
        )

        run_until_idle(rt.worker(stages))

        assert answers[0]["state"] == "running"
        assert answers[0]["cancel_requested"] is True

    def test_a_waiting_run_is_cancelled_and_its_gate_closes(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        gate_id = rt.gate_id(run_id)

        cancelled = rt.cancel(run_id)
        late = rt.resolve(gate_id)

        assert cancelled.status_code == 202
        assert late.status_code == 409
        events = rt.events(run_id)
        assert names(events)[-1] == "job.finished"
        assert events[-1].data["outcome"] == "cancelled"
        assert [e.name for e in events].count("job.finished") == 1

    def test_a_finished_run_cannot_be_cancelled(self, rt):
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        assert rt.cancel(run_id).status_code == 409

    def test_a_viewer_may_not_cancel(self, rt):
        run_id = rt.start_run()

        assert rt.cancel(run_id, browser=rt.viewer).status_code == 403

    def test_cancelling_is_audited_with_ids_only(self, rt):
        run_id = rt.start_run()

        rt.cancel(run_id)

        cancelled = [e for e in audit_events(rt.harness) if e.entry.action == "run.cancelled"]
        assert [(e.entry.target_type, e.entry.target_id) for e in cancelled] == [
            ("run", run_id)
        ]


class TestFailure:
    def test_an_unexpected_error_fails_the_run_in_plain_language(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        stages = simple_stages("queries")
        stages["discover"] = FailingStage()

        run_until_idle(rt.worker(stages))

        finished = rt.events(run_id)[-1]
        assert finished.name == "job.finished"
        assert finished.data["outcome"] == "failed"
        error = finished.data["error"]
        assert error.startswith("The Discover stage stopped")
        for leak in ("Traceback", "KeyError", "secret", "10.0.0.7"):
            assert leak not in error

    def test_a_stage_can_explain_its_own_failure(self, rt):
        run_id = rt.start_run(["queries"])
        stages = {"queries": failing_with_message("The model server isn't answering.")}

        run_until_idle(rt.worker(stages))

        assert rt.events(run_id)[-1].data == {
            "outcome": "failed",
            "seconds": 0.0,
            "error": "The model server isn't answering.",
        }

    def test_a_failure_keeps_the_stages_that_finished(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        stages = simple_stages("queries")
        stages["discover"] = failing_with_message("The search source is down.")

        run_until_idle(rt.worker(stages))

        events = rt.events(run_id)
        finished = [e.data["stage"] for e in events if e.name == "stage.finished"]
        assert finished == ["queries"]
        assert rt.stored_run(run_id).stages_finished == 1
        assert rt.stored_run(run_id).state is RunState.FAILED


class TestAccess:
    """Architecture §4.4 and §5.5: a non-member learns nothing, not even that an id exists."""

    def _routes(self, rt: RunTeam) -> list[tuple[str, str, str]]:
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        gate_id = rt.gate_id(run_id)
        return [
            ("POST", f"/api/v1/reviews/{rt.review_id}/runs", f"/api/v1/reviews/{MISSING}/runs"),
            ("GET", f"/api/v1/reviews/{rt.review_id}/runs", f"/api/v1/reviews/{MISSING}/runs"),
            ("GET", f"/api/v1/runs/{run_id}/events", f"/api/v1/runs/{MISSING}/events"),
            ("POST", f"/api/v1/runs/{run_id}/cancel", f"/api/v1/runs/{MISSING}/cancel"),
            ("POST", f"/api/v1/gates/{gate_id}", f"/api/v1/gates/{MISSING}"),
        ]

    def test_a_non_member_gets_404_on_every_run_gate_and_event_route(self, rt):
        failures = []
        for method, path, _ in self._routes(rt):
            for browser in (rt.outsider, rt.team.admin):
                body = {} if method == "POST" else None
                response = send(browser, method, path, body)
                if response.status_code != 404:
                    failures.append((method, path, response.status_code))

        assert failures == []

    def test_a_non_member_cannot_tell_an_existing_id_from_a_missing_one(self, rt):
        failures = []
        for method, path, missing in self._routes(rt):
            body = {} if method == "POST" else None
            existing = send(rt.outsider, method, path, body)
            absent = send(rt.outsider, method, missing, body)
            if (existing.status_code, existing.json()) != (absent.status_code, absent.json()):
                failures.append((method, path))

        assert failures == []

    def test_a_member_removed_from_the_project_loses_the_run_at_once(self, rt):
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))
        kofi = rt.team.user_id("kofi@example.org")
        asyncio.run(_remove_member(rt, UUID(kofi)))

        assert rt.stream(run_id, browser=rt.reviewer).status_code == 404

    def test_a_signed_out_browser_gets_401_on_the_stream(self, rt):
        run_id = rt.start_run(["queries"])

        assert rt.harness.browser().get(f"/api/v1/runs/{run_id}/events").status_code == 401


async def _remove_member(rt: RunTeam, user_id: UUID) -> None:
    async with rt.harness.providers.storage.unit_of_work() as uow:
        await uow.memberships.remove(UUID(rt.project_id), user_id)
        await uow.commit()


class TestPrivacy:
    def test_no_raw_question_text_appears_in_events_or_audit_rows(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker(_gated()))
        rt.resolve(rt.gate_id(run_id))
        run_until_idle(rt.worker(_gated()))
        rt.cancel(rt.start_run(["queries"]))

        everything = repr([e.data for e in rt.events(run_id)])
        audit = repr([e.entry for e in audit_events(rt.harness)])
        for fragment in ("malnutrition", "Marib", QUESTION):
            assert fragment not in everything
            assert fragment not in audit

    def test_event_payloads_are_stored_only_as_ciphertext(self, rt):
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        stored = repr(vars(rt.harness.storage.state))
        assert len(rt.events(run_id)) == 5
        assert '"stage": "queries"' not in stored
        assert "Item 1" not in stored

    def test_an_event_payload_cannot_be_moved_to_another_event(self, rt):
        """The payload is bound to its run and its id, so the log cannot be reordered."""
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        async def swap() -> None:
            providers = rt.harness.providers
            async with providers.storage.unit_of_work() as uow:
                first = (await uow.run_events.list_after(UUID(run_id), 0, limit=1))[0]
                await providers.encryption.decrypt(
                    uow, event_context(rt.project_id, run_id, 2), first.payload_sealed
                )

        with pytest.raises(DecryptionError):
            asyncio.run(swap())

    def test_deleting_the_project_makes_every_event_unreadable(self, rt):
        """Crypto-shredding: the log is project data and dies with the project's key."""
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker(simple_stages("queries")))

        deleted = send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}")

        assert deleted.status_code == 204
        with pytest.raises(KeyDestroyedError):
            rt.events(run_id)
        assert rt.stream(run_id).status_code == 404


def test_a_review_in_one_project_cannot_be_run_from_another_projects_membership():
    """Membership is checked against the run's own project, never the caller's other ones."""
    rt = make_run_team()
    other = create_review(
        rt.outsider, send(rt.outsider, "POST", PROJECTS, {"name": "Mine"}).json()["id"]
    )

    response = send(
        rt.outsider, "POST", f"/api/v1/reviews/{rt.review_id}/runs", {"stages": ["queries"]}
    )
    own = send(
        rt.outsider, "POST", f"/api/v1/reviews/{other['id']}/runs", {"stages": ["queries"]}
    )

    assert (response.status_code, own.status_code) == (404, 202)


def test_an_unknown_review_is_404(rt):
    response = send(
        rt.owner, "POST", f"/api/v1/reviews/{uuid.uuid4()}/runs", {"stages": ["queries"]}
    )

    assert response.status_code == 404
