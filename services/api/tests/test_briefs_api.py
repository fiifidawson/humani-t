"""Reading a decision brief: the brief, one cited source, and how it was made.

The brief is read-only, and a non-member gets the same 404 as a missing id.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.briefs import CAVEAT
from tests.brief_support import (
    MASKED_PLACE,
    SENSITIVE_PLACE,
    RoleScript,
    answer_stage,
    critic_answer,
    planner_answer,
    write_brief,
    writer_answer,
)
from tests.run_support import RunTeam, make_run_team


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def brief_path(rt: RunTeam, run_id: str | None = None) -> str:
    query = "" if run_id is None else f"?run_id={run_id}"
    return f"/api/v1/reviews/{rt.review_id}/brief{query}"


def read_brief(
    rt: RunTeam, run_id: str | None = None, browser: TestClient | None = None
) -> dict[str, Any]:
    response = (browser or rt.viewer).get(brief_path(rt, run_id))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestReadingTheBrief:
    def test_any_member_reads_the_latest_brief(self, rt):
        run_id = write_brief(rt)

        body = read_brief(rt, browser=rt.viewer)

        assert body["run_id"] == run_id
        assert body["version"] == 1
        assert [o["name"] for o in body["options"]] == ["Cash transfers", "In-kind food"]

    def test_the_brief_carries_the_fixed_caveat_model_rounds_and_stop_reason(self, rt):
        write_brief(rt)

        body = read_brief(rt)

        assert body["caveat"] == CAVEAT
        assert (body["model_id"], body["rounds"], body["kept_round"], body["max_rounds"]) == (
            "qwen3.5:9b",
            1,
            1,
            3,
        )
        assert body["stopped_because"] == "accepted"
        assert body["warnings"] == []

    def test_the_question_is_shown_as_it_was_sent_masked(self, rt):
        write_brief(rt)

        body = read_brief(rt)

        assert MASKED_PLACE in body["question"]
        assert SENSITIVE_PLACE not in json.dumps(body)

    def test_claims_say_whether_they_are_evidence_or_reasoning(self, rt):
        write_brief(rt)

        cash = read_brief(rt)["options"][0]

        assert cash["strength"] == "moderate"
        assert cash["benefits"][0] == {
            "text": "Better diet diversity",
            "support": "evidence",
            "citations": [{"tag": "P0001", "source_kind": "paper"}],
        }
        assert read_brief(rt)["options"][1]["risks"][0]["support"] == "hypothesis"
        assert read_brief(rt)["options"][1]["risks"][0]["citations"] == []

    def test_trade_off_rows_mark_the_humanitarian_principles(self, rt):
        write_brief(rt)

        rows = read_brief(rt)["trade_offs"]

        assert [(r["criterion"], r["is_humanitarian"]) for r in rows] == [
            ("Do no harm", True),
            ("Food security", False),
        ]
        assert [a["option"] for a in rows[0]["assessments"]] == [
            "Cash transfers",
            "In-kind food",
        ]

    def test_conflicts_considerations_and_gaps_are_returned(self, rt):
        write_brief(rt)

        body = read_brief(rt)

        assert body["conflicts"][0]["conflict_type"] == "context"
        assert len(body["conflicts"][0]["positions"]) == 2
        assert body["conflicts"][0]["meaning"]["support"] == "hypothesis"
        assert body["considerations"][0]["option"] == "In-kind food"
        assert body["gaps"] == ["Nothing on people with disabilities."]

    def test_the_sources_list_every_cited_tag_in_order_of_first_appearance(self, rt):
        write_brief(rt)

        sources = read_brief(rt)["sources"]

        assert [s["tag"] for s in sources] == ["P0003", "P0001", "P0002", "P0004"]
        first = next(s for s in sources if s["tag"] == "P0001")
        assert first["title"] == "Cash transfers and food prices in small markets"
        assert first["screening_label"] == "include"
        assert first["citations"] == 3

    def test_the_critics_scores_are_returned_without_a_total(self, rt):
        write_brief(rt)

        quality = read_brief(rt)["quality"]

        assert set(quality) == {
            "coverage",
            "groundedness",
            "disagreements",
            "uncertainty",
            "humanitarian_awareness",
            "specificity",
        }

    def test_nothing_in_the_brief_names_a_winner_a_rank_or_a_total(self, rt):
        write_brief(rt)

        keys = _keys(read_brief(rt))

        assert [
            k for k in keys if any(w in k for w in ("winner", "rank", "recommend", "total"))
        ] == []

    def test_a_given_runs_brief_is_returned_when_asked_for(self, rt):
        first = write_brief(rt)
        write_brief(rt)

        assert read_brief(rt)["version"] == 2
        assert read_brief(rt, first)["version"] == 1

    def test_a_review_with_no_brief_yet_is_404(self, rt):
        response = rt.viewer.get(brief_path(rt))

        assert response.status_code == 404

    def test_a_run_of_another_review_is_404(self, rt):
        write_brief(rt)

        response = rt.viewer.get(brief_path(rt, str(uuid.uuid4())))

        assert response.status_code == 404

    def test_a_non_member_gets_the_same_404_as_for_a_missing_review(self, rt):
        write_brief(rt)

        existing = rt.outsider.get(brief_path(rt))
        missing = rt.outsider.get(f"/api/v1/reviews/{uuid.uuid4()}/brief")

        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()

    def test_failed_checks_are_returned_as_warnings(self, rt):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005")],
            critic=[critic_answer()],
        )
        write_brief(rt, answer_stage(rt, script, max_rounds=1))

        body = read_brief(rt)

        assert body["stopped_because"] == "max_rounds"
        assert [w["code"] for w in body["warnings"]] == ["citation_not_retrieved"]


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [k for k, v in value.items() for k in (k, *_keys(v))]
    if isinstance(value, list):
        return [k for item in value for k in _keys(item)]
    return []


def evidence_path(brief_id: str, tag: str) -> str:
    return f"/api/v1/briefs/{brief_id}/evidence/{tag}"


class TestTheSourcePanel:
    def test_a_cited_tag_opens_its_masked_passage_and_the_search_that_found_it(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        response = rt.viewer.get(evidence_path(brief_id, "P0001"))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tag"] == "P0001"
        assert body["source_kind"] == "paper"
        [passage] = body["passages"]
        assert MASKED_PLACE in passage["text"]
        assert passage["found_by"]
        assert passage["locator"] == "abstract"

    def test_the_panel_shows_the_papers_details_and_why_it_was_included(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        source = rt.viewer.get(evidence_path(brief_id, "P0001")).json()["source"]

        assert source["title"] == "Cash transfers and food prices in small markets"
        assert source["year"] == 2023
        assert source["rationale"] == "Reports price effects of cash transfers in camp markets."

    def test_the_details_are_omitted_when_the_pipeline_had_none(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        body = rt.viewer.get(evidence_path(brief_id, "P0002")).json()

        assert body["source"] is None

    def test_cited_for_lists_every_claim_citing_the_tag_and_where_it_is(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        cited_for = rt.viewer.get(evidence_path(brief_id, "P0001")).json()["cited_for"]

        assert {(c["text"], c["section"], c["option"]) for c in cited_for} == {
            ("Better diet diversity", "benefit", "Cash transfers"),
            ("Prices can rise in small markets", "risk", "Cash transfers"),
            ("Yes, in small markets", "conflict", None),
        }

    def test_a_tag_the_brief_has_no_passage_for_is_404(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        assert rt.viewer.get(evidence_path(brief_id, "P0005")).status_code == 404

    def test_a_non_member_gets_the_same_404_as_for_a_missing_brief(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        existing = rt.outsider.get(evidence_path(brief_id, "P0001"))
        missing = rt.outsider.get(evidence_path(str(uuid.uuid4()), "P0001"))

        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()


class TestHowThisWasMade:
    def test_the_trace_lists_every_step_in_order(self, rt):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer()],
            critic=[critic_answer("revise", queries=["earthquake shelter"]), critic_answer()],
        )
        write_brief(rt, answer_stage(rt, script))
        brief_id = read_brief(rt)["id"]

        response = rt.viewer.get(f"/api/v1/briefs/{brief_id}/trace")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [(s["round"], s["role"], s["status"]) for s in body["steps"]] == [
            (0, "planner", "ok"),
            (0, "retriever", "ok"),
            (1, "writer", "ok"),
            (1, "checks", "ok"),
            (1, "critic", "ok"),
            (1, "retriever", "ok"),
            (2, "writer", "ok"),
            (2, "checks", "ok"),
            (2, "critic", "ok"),
        ]
        assert (body["stopped_because"], body["kept_round"], body["rounds"]) == (
            "accepted",
            2,
            2,
        )

    def test_the_trace_matches_the_steps_the_run_streamed(self, rt):
        run_id = write_brief(rt)
        brief_id = read_brief(rt)["id"]

        steps = rt.viewer.get(f"/api/v1/briefs/{brief_id}/trace").json()["steps"]

        streamed = [e.data for e in rt.events(run_id) if e.name == "agent.step"]
        assert steps == streamed

    def test_the_trace_totals_the_model_calls_and_tokens(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        body = rt.viewer.get(f"/api/v1/briefs/{brief_id}/trace").json()

        assert body["model_id"] == "qwen3.5:9b"
        assert body["totals"]["calls"] == 3
        assert body["totals"]["prompt_tokens"] > 0
        assert body["totals"]["completion_tokens"] > 0
        assert body["manifest"]["prompt_versions"]["planner"]

    def test_a_non_member_gets_the_same_404_as_for_a_missing_brief(self, rt):
        write_brief(rt)
        brief_id = read_brief(rt)["id"]

        existing = rt.outsider.get(f"/api/v1/briefs/{brief_id}/trace")
        missing = rt.outsider.get(f"/api/v1/briefs/{uuid.uuid4()}/trace")

        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()
