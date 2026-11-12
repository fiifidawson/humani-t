"""Requirements for the `queries` stage: the accepted plan, compiled once per source.

Only a confirmed vocabulary is searched; a run started on an unconfirmed one asks for it at
the `vocabulary` gate. The stage runs in the real `RunWorker`, and the model is a
`FakeInferenceProvider`.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.application.queries_stage import QueriesStage
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.search.arxiv import compile_arxiv_query
from app.search.openalex import compile_openalex_query
from tests.plan_support import PlanTeam, make_plan_team
from tests.project_support import send
from tests.run_support import run_until_idle

COMPILERS = {"openalex": compile_openalex_query, "arxiv": compile_arxiv_query}


@pytest.fixture
def team() -> PlanTeam:
    return make_plan_team()


def _run_queries(team: PlanTeam) -> str:
    run_id = team.rt.start_run(["queries"])
    _drain(team)
    return run_id


def _drain(team: PlanTeam) -> None:
    stage = QueriesStage(team.plans, COMPILERS)
    run_until_idle(team.rt.worker({"queries": stage}))


def _finished(team: PlanTeam, run_id: str) -> dict[str, Any]:
    finished = [e for e in team.rt.events(run_id) if e.name == "stage.finished"]
    assert finished, [e.name for e in team.rt.events(run_id)]
    summary: dict[str, Any] = finished[-1].data["summary"]
    return summary


def _outcome(team: PlanTeam, run_id: str) -> dict[str, Any]:
    data: dict[str, Any] = [e for e in team.rt.events(run_id) if e.name == "job.finished"][
        -1
    ].data
    return data


def _resolve(team: PlanTeam, run_id: str, decision: str, revisions: dict | None = None):
    body: dict[str, Any] = {"decision": decision}
    if revisions is not None:
        body["revisions"] = revisions
    return send(team.owner, "POST", f"/api/v1/gates/{team.rt.gate_id(run_id)}", body)


class TestWithAnAcceptedPlan:
    def test_the_plan_is_compiled_once_per_enabled_source_and_recorded(self, team):
        team.drafted()
        team.accept(1)

        run_id = _run_queries(team)

        summary = _finished(team, run_id)
        manifest = summary["manifest"]
        plan = team.plan()["current"]
        query = compile_vocabulary(
            Vocabulary(
                (
                    Concept("cash transfers", ("cash assistance",), theme="intervention"),
                    Concept("malnutrition", ("wasting",), theme="outcome"),
                ),
                VocabularyOrigin.LLM_ASSISTED,
            )
        )
        assert manifest["vocabulary_version"] == 1
        assert manifest["criteria_version"] == 1
        assert manifest["vocabulary_origin"] == "llm_assisted"
        assert manifest["compiled_queries"] == {
            "openalex": compile_openalex_query(query),
            "arxiv": compile_arxiv_query(query),
        }
        assert manifest["sources"] == ["openalex", "arxiv"]
        assert manifest["plan_model_id"] == plan["model_id"]
        assert manifest["plan_prompt_version"] == plan["prompt_version"]
        assert _outcome(team, run_id)["outcome"] == "done"

    def test_no_gate_is_asked_for_a_confirmed_vocabulary(self, team):
        team.drafted()
        team.accept(1)

        run_id = _run_queries(team)

        assert "gate.required" not in [e.name for e in team.rt.events(run_id)]

    def test_the_summary_shows_counts_and_no_question_text(self, team):
        team.drafted()
        team.accept(1)

        run_id = _run_queries(team)

        text = str([e.data for e in team.rt.events(run_id)])
        assert "Marib" not in text
        assert _finished(team, run_id)["concepts"] == 2


class TestWithoutAnAcceptedPlan:
    def test_an_unconfirmed_draft_stops_the_run_at_the_vocabulary_gate(self, team):
        team.drafted()

        run_id = _run_queries(team)

        required = [e for e in team.rt.events(run_id) if e.name == "gate.required"]
        assert [e.data["kind"] for e in required] == ["vocabulary"]
        payload = required[0].data["payload"]
        assert payload["version"] == 1
        assert payload["origin"] == "llm_assisted"
        assert [t["name"] for t in payload["themes"]] == ["intervention", "outcome"]
        assert payload["rendered"].startswith('("cash transfers"')
        assert team.rt.stored_run(run_id).state.value == "waiting"

    def test_approving_the_gate_accepts_the_draft_and_the_run_goes_on(self, team):
        team.drafted()
        run_id = _run_queries(team)

        assert _resolve(team, run_id, "approve").status_code == 200
        _drain(team)

        assert _outcome(team, run_id)["outcome"] == "done"
        plan = team.plan()
        assert plan["accepted_version"] == 1
        assert plan["current"]["accepted_by"] == team.rt.team.user_id("amara@example.org")
        assert _finished(team, run_id)["manifest"]["vocabulary_version"] == 1

    def test_approving_with_revisions_accepts_the_persons_edit_as_a_new_version(self, team):
        team.drafted()
        run_id = _run_queries(team)
        revisions = {
            "vocabulary": {
                "concepts": [
                    {
                        "term": "cash transfers",
                        "synonyms": ["vouchers"],
                        "theme": "intervention",
                    }
                ]
            }
        }

        _resolve(team, run_id, "approve", revisions)
        _drain(team)

        current = team.plan()["current"]
        assert current["version"] == 2
        assert current["author"]["kind"] == "person"
        assert current["accepted_at"] is not None
        assert [c["id"] for c in current["criteria"]] == ["I1", "E1"]
        manifest = _finished(team, run_id)["manifest"]
        assert manifest["vocabulary_version"] == 2
        assert "vouchers" in manifest["compiled_queries"]["openalex"]

    def test_rejecting_the_gate_fails_the_run_in_plain_words(self, team):
        team.drafted()
        run_id = _run_queries(team)

        _resolve(team, run_id, "reject")
        _drain(team)

        outcome = _outcome(team, run_id)
        assert outcome["outcome"] == "failed"
        assert "search plan" in outcome["error"]
        assert team.plan()["accepted_version"] is None

    def test_with_no_plan_at_all_the_stage_drafts_one_from_the_masked_question(self, team):
        run_id = _run_queries(team)

        assert len(team.model.requests) == 1
        assert "Marib" not in team.model.sent_text()
        assert team.plan()["current"]["version"] == 1
        assert [
            e.data["kind"] for e in team.rt.events(run_id) if e.name == "gate.required"
        ] == ["vocabulary"]

    def test_running_the_stage_again_after_the_gate_does_not_draft_again(self, team):
        run_id = _run_queries(team)
        _resolve(team, run_id, "approve")
        _drain(team)

        assert len(team.model.requests) == 1
        assert _outcome(team, run_id)["outcome"] == "done"

    def test_a_strict_vocabulary_goes_through_the_gate_without_the_model(self, team):
        team.upload()
        run_id = _run_queries(team)
        _resolve(team, run_id, "approve")
        _drain(team)

        assert team.model.requests == []
        manifest = _finished(team, run_id)["manifest"]
        assert manifest["vocabulary_origin"] == "user_supplied"
        assert manifest["plan_model_id"] is None


class TestPlanningDuringARun:
    def test_the_plan_cannot_change_while_a_run_waits_at_its_gate(self, team):
        team.drafted()
        _run_queries(team)

        assert team.change("add vouchers", based_on_version=1).status_code == 409
        assert team.upload().status_code == 409
        assert team.accept(1).status_code == 409
