"""Requirements for Needs you, Recent activity and the project timeline over HTTP.

UI_UX_SPEC §7.1 and §7.4. Everything is derived from records the pipeline already keeps;
only a person's dismissals are stored. Nothing is ever shown about a project the caller is
not a member of: not its name, not its counts, not that it exists.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.runs import GateKind
from app.runs.fake import FakeStage
from tests.brief_support import write_brief
from tests.history_support import (
    MODEL,
    ModelStage,
    accept_draft,
    add_draft,
    second_project,
)
from tests.project_support import (
    OWNER_EMAIL,
    PROJECT_NAME,
    REVIEWER_EMAIL,
    send,
)
from tests.run_support import (
    QUESTION,
    RunTeam,
    failing_with_message,
    make_run_team,
    run_until_idle,
)

NEEDS_YOU = "/api/v1/needs-you"
DISMISSALS = "/api/v1/needs-you/dismissals"
ACTIVITY = "/api/v1/activity"
STOPPED = "The model server did not answer."


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def needs_you(browser: Any) -> list[dict[str, Any]]:
    response = browser.get(NEEDS_YOU)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["count"] == len(body["items"])
    items: list[dict[str, Any]] = body["items"]
    return items


def kinds(browser: Any) -> list[str]:
    return [item["kind"] for item in needs_you(browser)]


def open_gate(rt: RunTeam, kind: GateKind = GateKind.VOCABULARY) -> str:
    stage = "queries" if kind is GateKind.VOCABULARY else "mask"
    run_id = rt.start_run([stage])
    run_until_idle(rt.worker({stage: FakeStage(items=1, gate=kind)}))
    return run_id


def fail_run(rt: RunTeam) -> str:
    run_id = rt.start_run(["queries", "discover"])
    run_until_idle(
        rt.worker({"queries": FakeStage(items=1), "discover": failing_with_message(STOPPED)})
    )
    return run_id


def dismiss(browser: Any, key: str) -> Any:
    return send(browser, "POST", DISMISSALS, {"key": key})


def history(rt: RunTeam, browser: Any = None) -> Any:
    return (browser or rt.owner).get(f"/api/v1/projects/{rt.project_id}/history")


class TestNeedsYou:
    def test_someone_with_no_projects_needs_to_do_nothing(self, rt):
        response = rt.outsider.get(NEEDS_YOU)

        assert response.status_code == 200
        assert response.json() == {"count": 0, "items": []}

    def test_an_open_gate_waits_for_the_members_who_may_decide_it(self, rt):
        run_id = open_gate(rt)

        (item,) = needs_you(rt.owner)

        assert item["kind"] == "gate"
        assert item["gate_kind"] == "vocabulary"
        assert item["project"] == {"id": rt.project_id, "name": PROJECT_NAME}
        assert item["review_id"] == rt.review_id
        assert item["run_id"] == run_id
        assert item["key"] == f"gate:{item['gate_id']}"
        assert kinds(rt.reviewer) == ["gate"]
        assert kinds(rt.viewer) == []
        assert kinds(rt.outsider) == []

    def test_a_privacy_review_waits_only_for_owners(self, rt):
        open_gate(rt, GateKind.PRIVACY_REVIEW)

        assert kinds(rt.owner) == ["gate"]
        assert kinds(rt.reviewer) == []

    def test_a_decided_gate_leaves_the_list(self, rt):
        run_id = open_gate(rt)

        assert rt.resolve(rt.gate_id(run_id)).status_code == 200

        assert kinds(rt.owner) == []

    def test_a_failed_run_says_where_it_stopped(self, rt):
        run_id = fail_run(rt)

        (item,) = needs_you(rt.reviewer)

        assert item["kind"] == "failed_run"
        assert item["run_id"] == run_id
        assert item["run_number"] == 1
        assert item["stopped_at"] == "discover"
        assert kinds(rt.viewer) == []

    def test_a_failed_run_leaves_once_a_newer_run_starts(self, rt):
        fail_run(rt)
        rt.clock.advance(minutes=5)

        rt.start_run(["queries"])

        assert kinds(rt.owner) == []

    def test_a_draft_waiting_for_accept_waits_for_contributors(self, rt):
        add_draft(rt.harness, rt.review_id)

        (item,) = needs_you(rt.reviewer)

        assert item["kind"] == "draft"
        assert item["version"] == 1
        assert item["key"] == f"draft:{rt.review_id}:1"
        assert kinds(rt.viewer) == []

    def test_an_accepted_draft_leaves_the_list(self, rt):
        add_draft(rt.harness, rt.review_id)

        accept_draft(rt.harness, rt.review_id, rt.team.user_id(OWNER_EMAIL))

        assert kinds(rt.owner) == []

    def test_a_brief_waits_for_every_member_until_they_open_it(self, rt):
        write_brief(rt)
        (item,) = needs_you(rt.viewer)
        assert item["kind"] == "unread_brief"
        assert item["version"] == 1

        response = send(rt.viewer, "PUT", f"/api/v1/briefs/{item['brief_id']}/seen")

        assert response.status_code == 204
        assert kinds(rt.viewer) == []
        assert kinds(rt.owner) == ["unread_brief"]

    def test_only_the_newest_brief_of_a_review_is_listed(self, rt):
        write_brief(rt)
        rt.clock.advance(minutes=5)
        write_brief(rt)

        (item,) = needs_you(rt.owner)

        assert item["version"] == 2

    def test_items_come_newest_first(self, rt):
        write_brief(rt)
        rt.clock.advance(minutes=5)
        fail_run(rt)

        assert kinds(rt.owner) == ["failed_run", "unread_brief"]

    def test_archived_projects_are_left_out(self, rt):
        fail_run(rt)

        send(rt.owner, "POST", f"/api/v1/projects/{rt.project_id}/archive")

        assert kinds(rt.owner) == []


class TestDismiss:
    def test_dismiss_hides_an_item_for_that_person_only(self, rt):
        fail_run(rt)
        (item,) = needs_you(rt.owner)

        response = dismiss(rt.owner, item["key"])

        assert response.status_code == 204
        assert kinds(rt.owner) == []
        assert kinds(rt.reviewer) == ["failed_run"]

    def test_dismissing_twice_changes_nothing(self, rt):
        fail_run(rt)
        (item,) = needs_you(rt.owner)
        dismiss(rt.owner, item["key"])

        assert dismiss(rt.owner, item["key"]).status_code == 204

    def test_a_dismissed_failure_comes_back_when_a_new_run_fails(self, rt):
        fail_run(rt)
        (first,) = needs_you(rt.owner)
        dismiss(rt.owner, first["key"])

        rt.clock.advance(minutes=5)
        fail_run(rt)

        (again,) = needs_you(rt.owner)
        assert again["kind"] == "failed_run"
        assert again["run_number"] == 2

    def test_an_item_the_caller_cannot_see_cannot_be_dismissed(self, rt):
        fail_run(rt)
        (item,) = needs_you(rt.owner)

        assert dismiss(rt.outsider, item["key"]).status_code == 404
        assert dismiss(rt.viewer, item["key"]).status_code == 404
        assert dismiss(rt.owner, "failed_run:not-a-run").status_code == 404

    def test_a_dismissal_needs_a_key(self, rt):
        assert send(rt.owner, "POST", DISMISSALS, {}).status_code == 422


class TestRecentActivity:
    def test_lines_cover_the_review_its_runs_and_its_brief_newest_first(self, rt):
        rt.clock.advance(minutes=1)
        fail_run(rt)
        rt.clock.advance(minutes=1)
        write_brief(rt)

        response = rt.reviewer.get(ACTIVITY)

        assert response.status_code == 200
        lines = response.json()["events"]
        # The brief is written inside its run, so the run finishes just after it.
        assert [line["action"] for line in lines] == [
            "run",
            "brief_ready",
            "run",
            "review_started",
        ]
        assert [line["at"] for line in lines] == sorted(
            (line["at"] for line in lines), reverse=True
        )
        assert lines[0]["project"] == {"id": rt.project_id, "name": PROJECT_NAME}
        assert lines[0]["run_state"] == "done"
        assert lines[2]["run_state"] == "failed"
        assert lines[3]["actor"]["display_name"] == OWNER_EMAIL.split("@")[0]
        assert lines[3]["text"] == QUESTION

    def test_a_follow_up_question_names_who_asked_it(self, rt):
        rt.clock.advance(minutes=1)
        asked = send(
            rt.reviewer,
            "POST",
            f"/api/v1/reviews/{rt.review_id}/questions",
            {"question": "How fast do prices rise after a distribution?"},
        )
        assert asked.status_code == 202, asked.text

        line = rt.owner.get(ACTIVITY).json()["events"][0]

        assert line["action"] == "question_asked"
        assert line["kind"] == "question"
        assert line["actor"]["display_name"] == REVIEWER_EMAIL.split("@")[0]
        assert line["text"] == "How fast do prices rise after a distribution?"

    def test_activity_from_every_project_of_the_caller_is_merged(self, rt):
        rt.clock.advance(minutes=1)
        other, _ = second_project(rt, "Market assessment, Mopti")

        projects = [line["project"]["id"] for line in rt.owner.get(ACTIVITY).json()["events"]]

        assert projects == [other, rt.project_id]

    def test_an_outsider_sees_nothing_of_a_project_s_activity(self, rt):
        fail_run(rt)

        assert rt.outsider.get(ACTIVITY).json() == {"events": []}

    def test_a_project_the_caller_left_drops_out_of_their_activity(self, rt):
        reviewer_id = rt.team.user_id(REVIEWER_EMAIL)

        send(rt.reviewer, "DELETE", f"/api/v1/projects/{rt.project_id}/members/{reviewer_id}")

        assert rt.reviewer.get(ACTIVITY).json() == {"events": []}


class TestProjectTimeline:
    def test_a_run_names_its_model_and_expands_to_its_stages(self, rt):
        run_id = rt.start_run(["queries", "discover"])
        run_until_idle(rt.worker({"queries": FakeStage(items=1), "discover": ModelStage()}))

        response = history(rt, rt.viewer)

        assert response.status_code == 200
        (group,) = response.json()["groups"]
        assert group["review"]["id"] == rt.review_id
        assert group["review"]["question"] == QUESTION
        line = next(e for e in group["events"] if e["action"] == "run")
        assert line["run_id"] == run_id
        assert line["run_number"] == 1
        assert line["model_ids"] == [MODEL]
        assert [(s["stage"], s["state"]) for s in line["stages"]] == [
            ("queries", "done"),
            ("discover", "done"),
        ]

    def test_a_failed_run_says_where_it_stopped_and_why(self, rt):
        fail_run(rt)

        (group,) = history(rt).json()["groups"]
        line = next(e for e in group["events"] if e["action"] == "run")

        assert line["run_state"] == "failed"
        assert line["stopped_at"] == "discover"
        assert line["error"] == STOPPED

    def test_a_brief_line_names_its_model(self, rt):
        write_brief(rt)

        (group,) = history(rt).json()["groups"]
        line = next(e for e in group["events"] if e["action"] == "brief_ready")

        assert line["version"] == 1
        assert line["model_ids"] == ["qwen3.5:9b"]
        assert line["brief_id"]

    def test_drafts_and_accepts_are_review_events(self, rt):
        rt.clock.advance(minutes=1)
        add_draft(rt.harness, rt.review_id)
        rt.clock.advance(minutes=1)
        accept_draft(rt.harness, rt.review_id, rt.team.user_id(OWNER_EMAIL))

        (group,) = history(rt).json()["groups"]

        assert [(e["action"], e["kind"]) for e in group["events"]] == [
            ("plan_accepted", "review"),
            ("plan_drafted", "review"),
            ("review_started", "review"),
        ]
        assert group["events"][0]["version"] == 1

    def test_reviews_are_ordered_by_their_latest_activity(self, rt):
        rt.clock.advance(minutes=1)
        newer = send(
            rt.owner,
            "POST",
            f"/api/v1/projects/{rt.project_id}/reviews",
            {"question": "Do vouchers reduce protection risks?"},
        ).json()["id"]
        rt.clock.advance(minutes=1)
        fail_run(rt)

        groups = history(rt).json()["groups"]

        assert [g["review"]["id"] for g in groups] == [rt.review_id, newer]

    def test_a_non_member_gets_the_same_404_as_for_a_missing_project(self, rt):
        response = history(rt, rt.outsider)

        assert response.status_code == 404
