"""Brief versions, comparing two versions, and comparing two runs of one review.

Every comparison is computed by the API and reads from the older to the newer, whichever
order the ids are given in. Members only: a non-member gets the same 404 as a missing id.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.domain.runs import RunState
from tests.brief_support import (
    RoleScript,
    answer_stage,
    critic_answer,
    planner_answer,
    write_brief,
    writer_answer,
)
from tests.pipeline_support import (
    ALL_STAGES,
    CORPUS,
    SECOND_CORPUS,
    CorpusSource,
    ObservedInference,
    Pipeline,
    make_pipeline,
    reasonable_answer,
    with_answers,
)
from tests.project_support import PROJECTS, send
from tests.run_support import (
    FailingStage,
    RunTeam,
    create_review,
    make_run_team,
    run_until_idle,
)

_MISSING = "00000000-0000-4000-8000-000000000000"


def _renamed(answer: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    renamed: dict[str, Any] = json.loads(json.dumps(answer).replace(old, new))
    return renamed


def first_draft() -> dict[str, Any]:
    """Cash moderate, In-kind food weak; cites P0001 to P0003 but never P0004."""
    return _renamed(writer_answer(), '"P0004"', '"P0003"')


def second_draft() -> dict[str, Any]:
    """Cash strong, Vouchers in place of In-kind food; cites P0004 and never P0002."""
    draft = _renamed(
        _renamed(writer_answer(), "In-kind food", "Vouchers"), '"P0002"', '"P0001"'
    )
    draft["options"][0]["strength"] = "strong"
    return draft


def brief_with(rt: RunTeam, draft: dict[str, Any]) -> str:
    script = RoleScript(planner=[planner_answer()], writer=[draft], critic=[critic_answer()])
    run_id = write_brief(rt, answer_stage(rt, script))
    rt.clock.advance(minutes=5)
    return run_id


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


@pytest.fixture
def two_briefs(rt: RunTeam) -> tuple[RunTeam, str, str]:
    first = brief_with(rt, first_draft())
    second = brief_with(rt, second_draft())
    return rt, first, second


def versions(rt: RunTeam, browser: Any = None) -> Any:
    return (browser or rt.viewer).get(f"/api/v1/reviews/{rt.review_id}/brief/versions")


def brief_id(rt: RunTeam, run_id: str) -> str:
    body = rt.viewer.get(f"/api/v1/reviews/{rt.review_id}/brief?run_id={run_id}").json()
    found: str = body["id"]
    return found


def compare_briefs(rt: RunTeam, a: str, b: str, browser: Any = None) -> Any:
    return (browser or rt.viewer).get(f"/api/v1/briefs/{a}/compare/{b}")


class TestBriefVersions:
    def test_each_run_of_the_brief_stage_is_a_version_listed_newest_first(self, two_briefs):
        rt, first, second = two_briefs

        body = versions(rt).json()

        assert [(v["version"], v["run_id"], v["is_latest"]) for v in body] == [
            (2, second, True),
            (1, first, False),
        ]
        assert set(body[0]) == {
            "id",
            "version",
            "run_id",
            "created_at",
            "model_id",
            "is_latest",
        }
        assert body[0]["model_id"] == "qwen3.5:9b"

    def test_a_review_without_a_brief_has_no_versions(self, rt):
        response = versions(rt)

        assert (response.status_code, response.json()) == (200, [])

    def test_the_brief_is_still_the_latest_unless_a_run_is_named(self, two_briefs):
        rt, first, _ = two_briefs

        latest = rt.viewer.get(f"/api/v1/reviews/{rt.review_id}/brief").json()
        older = rt.viewer.get(f"/api/v1/reviews/{rt.review_id}/brief?run_id={first}").json()

        assert (latest["version"], older["version"]) == (2, 1)
        assert rt.viewer.get(f"/api/v1/briefs/{older['id']}/trace").status_code == 200

    def test_a_non_member_gets_404(self, two_briefs):
        rt, _, _ = two_briefs

        assert versions(rt, rt.outsider).status_code == 404


class TestCompareBriefVersions:
    def test_options_added_and_removed_are_named(self, two_briefs):
        rt, first, second = two_briefs

        body = compare_briefs(rt, brief_id(rt, first), brief_id(rt, second)).json()

        assert body["options_added"] == ["Vouchers"]
        assert body["options_removed"] == ["In-kind food"]

    def test_a_strength_change_reads_from_the_older_version_to_the_newer(self, two_briefs):
        rt, first, second = two_briefs

        body = compare_briefs(rt, brief_id(rt, first), brief_id(rt, second)).json()

        assert body["strength_changes"] == [
            {"option": "Cash transfers", "before": "moderate", "after": "strong"}
        ]

    def test_sources_added_and_removed_are_the_papers_each_version_cites(self, two_briefs):
        rt, first, second = two_briefs

        body = compare_briefs(rt, brief_id(rt, first), brief_id(rt, second)).json()

        assert [(s["tag"], s["paper_id"]) for s in body["sources_added"]] == [
            ("P0004", str(UUID(int=4)))
        ]
        assert [(s["tag"], s["paper_id"]) for s in body["sources_removed"]] == [
            ("P0002", str(UUID(int=2)))
        ]
        assert set(body["sources_added"][0]) == {"paper_id", "source_kind", "tag", "title"}

    def test_the_order_of_the_ids_does_not_change_the_direction(self, two_briefs):
        rt, first, second = two_briefs
        older, newer = brief_id(rt, first), brief_id(rt, second)

        forwards = compare_briefs(rt, older, newer).json()
        backwards = compare_briefs(rt, newer, older).json()

        assert forwards == backwards
        assert (forwards["before"]["version"], forwards["after"]["version"]) == (1, 2)
        assert forwards["before"]["id"] == older

    def test_a_version_compared_with_itself_is_refused(self, two_briefs):
        rt, first, _ = two_briefs
        same = brief_id(rt, first)

        assert compare_briefs(rt, same, same).status_code == 422

    def test_a_non_member_gets_404_as_for_a_missing_brief(self, two_briefs):
        rt, first, second = two_briefs
        a, b = brief_id(rt, first), brief_id(rt, second)

        outsider = compare_briefs(rt, a, b, rt.outsider)
        missing = compare_briefs(rt, a, _MISSING)

        assert outsider.status_code == missing.status_code == 404
        assert outsider.json() == compare_briefs(rt, _MISSING, b, rt.outsider).json()

    def test_after_the_project_is_deleted_the_comparison_is_gone(self, two_briefs):
        rt, first, second = two_briefs
        a, b = brief_id(rt, first), brief_id(rt, second)
        assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204

        assert compare_briefs(rt, a, b, rt.owner).status_code == 404
        assert versions(rt, rt.owner).status_code == 404


def compare_runs(rt: RunTeam, a: str, b: str, browser: Any = None) -> Any:
    return (browser or rt.viewer).get(f"/api/v1/runs/{a}/compare/{b}")


def vouchers_excluded(request: Any) -> dict[str, Any]:
    """`reasonable_answer`, except that vouchers are now judged out of scope."""
    if "Vouchers" in request.messages[-1].content:
        return with_answers(
            request,
            {
                "label": "exclude",
                "score": 0.2,
                "priority": "low",
                "rationale": "Vouchers are not cash.",
                "criteria_matched": ["I1"],
            },
        )
    return reasonable_answer(request)


@pytest.fixture
def two_runs() -> tuple[Pipeline, str, str]:
    """Run 1 searches one source; run 2 adds a second and judges vouchers out."""
    p = make_pipeline(sources=[CorpusSource("first", CORPUS)])
    first = p.rt.start_run(ALL_STAGES)
    run_until_idle(p.rt.worker(p.stages()))
    p.rt.clock.advance(minutes=5)
    p.sources.append(CorpusSource("second", SECOND_CORPUS))
    p.model = ObservedInference(vouchers_excluded)
    second = p.rt.start_run(ALL_STAGES)
    run_until_idle(p.rt.worker(p.stages()))
    for run_id in (first, second):
        assert p.rt.stored_run(run_id).state is RunState.DONE, p.failure(run_id)
    return p, first, second


class TestCompareRuns:
    def test_both_runs_are_named_by_number_oldest_first(self, two_runs):
        p, first, second = two_runs

        body = compare_runs(p.rt, second, first).json()

        assert (body["before"]["run_id"], body["before"]["number"]) == (first, 1)
        assert (body["after"]["run_id"], body["after"]["number"]) == (second, 2)
        assert body["cannot_compare"] is None

    def test_setup_shows_the_sources_and_the_queries_added(self, two_runs):
        p, first, second = two_runs

        setup = compare_runs(p.rt, first, second).json()["setup"]

        assert setup["sources"] == {"before": ["first"], "after": ["first", "second"]}
        assert setup["vocabulary_versions"] == {"before": [3], "after": [3]}
        assert setup["model_ids"] == {"before": ["llama3.1:8b"], "after": ["llama3.1:8b"]}
        assert [q["source"] for q in setup["queries_added"]] == ["second"]
        assert setup["queries_removed"] == []
        assert setup["prompt_versions"] == [
            {"name": "screen", "before": "screen-3", "after": "screen-3"}
        ]

    def test_discovery_shows_counts_and_the_papers_only_one_run_found(self, two_runs):
        p, first, second = two_runs

        discovery = compare_runs(p.rt, first, second).json()["discovery"]

        assert discovery["before"] == {"identified": 4, "after_deduplication": 4}
        assert discovery["after"] == {"identified": 6, "after_deduplication": 5}
        assert discovery["only_before"] == []
        assert [paper["title"] for paper in discovery["only_after"]] == [
            "Mobile money and malnutrition after displacement"
        ]
        assert discovery["in_both"] == 4

    def test_screening_shows_label_counts_and_each_changed_label_with_its_reason(
        self, two_runs
    ):
        p, first, second = two_runs

        screening = compare_runs(p.rt, first, second).json()["screening"]

        assert screening["before"]["include"] == 2
        assert screening["after"]["exclude"] == 2
        [changed] = screening["changed"]
        assert changed["title"] == "Food vouchers in displacement camps"
        assert (changed["before"], changed["after"]) == ("include", "exclude")
        assert changed["reason"] == "Vouchers are not cash."

    def test_the_brief_part_is_empty_when_neither_run_wrote_a_brief(self, two_runs):
        p, first, second = two_runs

        assert compare_runs(p.rt, first, second).json()["brief"] is None

    def test_the_brief_part_compares_the_briefs_the_two_runs_wrote(self, rt):
        first = brief_with(rt, first_draft())
        second = brief_with(rt, second_draft())

        brief = compare_runs(rt, first, second).json()["brief"]

        assert brief["options_added"] == ["Vouchers"]
        assert brief["strength_changes"][0]["after"] == "strong"

    def test_a_run_with_no_record_of_its_setup_cannot_be_compared(self, rt):
        failed = rt.start_run(["answer"])
        run_until_idle(rt.worker({"answer": FailingStage()}))
        rt.clock.advance(minutes=5)
        briefed = brief_with(rt, first_draft())

        body = compare_runs(rt, briefed, failed).json()

        assert (
            body["cannot_compare"] == "Run 1 can't be compared: it has no record of its setup."
        )
        assert [body[part] for part in ("setup", "discovery", "screening", "brief")] == [
            None,
            None,
            None,
            None,
        ]

    def test_runs_of_two_reviews_cannot_be_compared(self, rt):
        here = brief_with(rt, first_draft())
        other_review = create_review(rt.owner, rt.project_id, "Another question?")["id"]
        there = send(
            rt.owner, "POST", f"/api/v1/reviews/{other_review}/runs", {"stages": ["answer"]}
        ).json()["run_id"]

        assert compare_runs(rt, here, there).status_code == 422

    def test_a_run_compared_with_itself_is_refused(self, rt):
        run_id = brief_with(rt, first_draft())

        assert compare_runs(rt, run_id, run_id).status_code == 422

    def test_a_non_member_gets_404_as_for_a_missing_run(self, two_runs):
        p, first, second = two_runs

        outsider = compare_runs(p.rt, first, second, p.rt.outsider)

        assert outsider.status_code == 404
        assert outsider.json() == compare_runs(p.rt, _MISSING, second, p.rt.outsider).json()
        assert compare_runs(p.rt, first, _MISSING).status_code == 404
