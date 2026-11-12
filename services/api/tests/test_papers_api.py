"""Requirements for the paper, PRISMA and manifest routes.

Members see masked text only, and a non-member gets the same 404 as a missing id.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.domain.briefs import SourceKind
from app.domain.encryption import KeyDestroyedError
from app.domain.inference import ChatRequest
from app.domain.runs import RunState
from app.domain.screening import Passage
from app.inference.fake import FakePromptGuard
from tests.brief_support import CORPUS as BRIEF_CORPUS
from tests.brief_support import (
    RoleScript,
    answer_stage,
    critic_answer,
    planner_answer,
    writer_answer,
)
from tests.pipeline_support import (
    ALL_STAGES,
    CORPUS,
    MASKED_PLACE,
    PLACE,
    Pipeline,
    make_pipeline,
    reasonable_answer,
    with_answers,
)
from tests.project_support import PROJECTS, send
from tests.run_support import run_until_idle

_MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def screened() -> tuple[Pipeline, str]:
    p = make_pipeline()
    run_id = p.rt.start_run(ALL_STAGES)
    run_until_idle(p.rt.worker(p.stages()))
    assert p.rt.stored_run(run_id).state is RunState.DONE
    return p, run_id


def _papers(p: Pipeline, query: str = "", browser: Any = None) -> Any:
    return (browser or p.rt.owner).get(f"/api/v1/reviews/{p.rt.review_id}/papers{query}")


class TestPaperList:
    def test_the_newest_runs_papers_are_listed_most_relevant_first(self, screened):
        p, run_id = screened

        body = _papers(p).json()

        assert body["run_id"] == run_id
        assert body["total"] == 5
        tiers = [row["decision"]["tier"] if row["decision"] else None for row in body["papers"]]
        assert tiers == ["highly_relevant", "highly_relevant", "relevant", "excluded", None]

    def test_the_filter_chips_count_every_tier(self, screened):
        p, _ = screened

        counts = _papers(p).json()["counts"]

        assert counts == {
            "highly_relevant": 2,
            "relevant": 1,
            "possibly_relevant": 0,
            "excluded": 1,
            "uncertain": 0,
            "unscreened": 1,
            "skipped": 1,
            "failed": 0,
            "not_screened": 0,
        }

    def test_a_row_carries_its_tag_metadata_and_decision(self, screened):
        p, _ = screened

        row = _papers(p).json()["papers"][0]

        assert row["tag"] == f"P{row['position']}"
        assert row["title"]
        assert row["found_by"]
        assert row["decision"]["model_id"] == "llama3.1:8b"
        assert row["decision"]["criteria_version"] == 3

    @pytest.mark.parametrize(
        ("label", "count"),
        [("include", 3), ("exclude", 1), ("uncertain", 0), ("unscreened", 1)],
    )
    def test_papers_can_be_filtered_by_label(self, screened, label, count):
        p, _ = screened

        body = _papers(p, f"?label={label}").json()

        assert body["total"] == count
        assert len(body["papers"]) == count

    def test_an_unknown_label_is_refused(self, screened):
        p, _ = screened

        assert _papers(p, "?label=maybe").status_code == 422

    def test_a_page_past_the_end_is_empty(self, screened):
        p, _ = screened

        body = _papers(p, "?page=2").json()

        assert (body["page"], body["total"], body["papers"]) == (2, 5, [])

    def test_the_list_never_carries_raw_text(self, screened):
        p, _ = screened

        response = _papers(p)

        assert PLACE not in response.text
        assert "abstract" not in response.json()["papers"][0]

    def test_a_viewer_may_read_the_papers(self, screened):
        p, _ = screened

        assert _papers(p, browser=p.rt.viewer).status_code == 200

    def test_a_non_member_gets_the_same_404_as_for_a_missing_review(self, screened):
        p, _ = screened

        theirs = _papers(p, browser=p.rt.outsider)
        missing = p.rt.outsider.get(f"/api/v1/reviews/{_MISSING}/papers")

        assert theirs.status_code == 404
        assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json())

    def test_a_run_of_another_review_is_not_found(self, screened):
        p, run_id = screened
        other = send(
            p.rt.owner,
            "POST",
            f"{PROJECTS}/{p.rt.project_id}/reviews",
            {"question": "Do vouchers work in floods?"},
        ).json()

        response = p.rt.owner.get(f"/api/v1/reviews/{other['id']}/papers?run_id={run_id}")

        assert response.status_code == 404

    def test_a_review_with_no_papers_yet_says_so(self):
        p = make_pipeline()

        response = _papers(p)

        assert response.status_code == 404
        assert "no papers" in response.json()["detail"]


class TestPaperDetail:
    def test_a_paper_shows_its_decision_with_full_provenance_and_masked_text(self, screened):
        p, run_id = screened
        first = _papers(p).json()["papers"][0]

        body = p.rt.owner.get(
            f"/api/v1/reviews/{p.rt.review_id}/papers/{first['paper_id']}"
        ).json()

        assert body["run_id"] == run_id
        assert body["text_kind"] == "abstract"
        decision = body["paper"]["decision"]
        assert set(decision) >= {
            "label",
            "priority",
            "score",
            "rationale",
            "criteria_matched",
            "exclusion_reasons",
            "model_id",
            "prompt_version",
            "criteria_version",
            "evidence_kind",
            "decided_at",
            "is_unreadable",
        }
        assert MASKED_PLACE in body["masked_text"]
        assert PLACE not in str(body)

    def test_an_excluded_paper_names_its_reasons(self, screened):
        p, _ = screened
        excluded = _papers(p, "?label=exclude").json()["papers"][0]

        assert excluded["decision"]["exclusion_reasons"] == ["E1", "I1"]

    def test_a_paper_the_run_did_not_see_is_not_found(self, screened):
        p, _ = screened

        response = p.rt.owner.get(f"/api/v1/reviews/{p.rt.review_id}/papers/{_MISSING}")

        assert response.status_code == 404

    def test_a_non_member_cannot_read_a_paper(self, screened):
        p, _ = screened
        first = _papers(p).json()["papers"][0]

        response = p.rt.outsider.get(
            f"/api/v1/reviews/{p.rt.review_id}/papers/{first['paper_id']}"
        )

        assert response.status_code == 404


class TestPrisma:
    def test_the_counts_match_what_export_recorded(self, screened):
        p, run_id = screened

        body = p.rt.owner.get(f"/api/v1/reviews/{p.rt.review_id}/prisma").json()

        exported = p.summary(run_id, "export")["prisma"]
        assert body["run_id"] == run_id
        assert {k: body[k] for k in exported} == exported

    def test_a_named_run_is_counted(self, screened):
        p, run_id = screened

        body = p.rt.owner.get(f"/api/v1/reviews/{p.rt.review_id}/prisma?run_id={run_id}").json()

        assert body["identified"] == 6


class TestManifest:
    def test_the_manifest_gathers_what_every_stage_recorded(self, screened):
        p, run_id = screened

        body = p.rt.owner.get(f"/api/v1/runs/{run_id}/manifest").json()

        assert body["run_id"] == run_id
        assert body["stages"] == list(ALL_STAGES)
        assert body["is_complete"]
        assert body["sources"] == ["first", "second"]
        assert body["vocabulary_versions"] == [3]
        assert body["criteria_versions"] == [3]
        assert body["model_ids"] == ["llama3.1:8b"]
        assert body["prompt_versions"] == {"screen": "screen-3"}
        assert set(body["queries"]) == {"first", "second"}
        assert [s["stage"] for s in body["stage_facts"]] == list(ALL_STAGES)

    def test_a_non_member_gets_404(self, screened):
        p, run_id = screened

        assert p.rt.outsider.get(f"/api/v1/runs/{run_id}/manifest").status_code == 404


class TestDeletedProject:
    def test_after_deletion_every_paper_route_answers_404(self, screened):
        p, run_id = screened
        assert send(p.rt.owner, "DELETE", f"{PROJECTS}/{p.rt.project_id}").status_code == 204

        paths = [
            f"/api/v1/reviews/{p.rt.review_id}/papers",
            f"/api/v1/reviews/{p.rt.review_id}/prisma",
            f"/api/v1/runs/{run_id}/manifest",
        ]

        assert [p.rt.owner.get(path).status_code for path in paths] == [404, 404, 404]


def _with_answers(request: ChatRequest, quote_from: str | None = None) -> dict[str, Any]:
    """`reasonable_answer`, its quotes taken from `quote_from` when given."""
    return with_answers(request, reasonable_answer(request), quote_from)


def _screen(script: Any = reasonable_answer, guard: FakePromptGuard | None = None) -> Pipeline:
    p = make_pipeline(script=script)
    if guard is not None:
        p.guard = guard
    p.rt.start_run(ALL_STAGES)
    run_until_idle(p.rt.worker(p.stages()))
    return p


class TestScreeningStatus:
    """Skipped and Failed are separate chips, never one bucket."""

    def test_each_row_says_whether_it_was_screened_skipped_or_failed(self, screened):
        p, _ = screened

        rows = _papers(p).json()["papers"]

        statuses = {row["title"]: row["screening_status"] for row in rows}
        assert statuses["Cash transfers and malnutrition: a trial protocol"] == "skipped"
        assert {s for t, s in statuses.items() if "protocol" not in t} == {"screened"}
        skipped = next(r for r in rows if r["screening_status"] == "skipped")
        assert skipped["skipped_because"] == "no_abstract"
        assert skipped["decision"] is None

    def test_papers_the_privacy_check_stopped_are_failed_not_skipped(self):
        p = _screen(guard=FakePromptGuard("1 PERSON entity"))

        body = _papers(p).json()

        assert body["counts"]["failed"] == 4
        assert body["counts"]["skipped"] == 1
        assert body["counts"]["unscreened"] == 5
        assert {r["screening_status"] for r in body["papers"]} == {"failed", "skipped"}

    @pytest.mark.parametrize(
        ("tier", "count"),
        [
            ("highly_relevant", 2),
            ("relevant", 1),
            ("possibly_relevant", 0),
            ("excluded", 1),
            ("uncertain", 0),
            ("skipped", 1),
            ("failed", 0),
            ("not_screened", 0),
        ],
    )
    def test_papers_can_be_filtered_by_each_tier(self, screened, tier, count):
        p, _ = screened

        body = _papers(p, f"?tier={tier}").json()

        assert body["total"] == count
        assert len(body["papers"]) == count

    def test_an_unknown_tier_is_refused(self, screened):
        p, _ = screened

        assert _papers(p, "?tier=maybe").status_code == 422

    def test_a_paper_not_reached_yet_is_not_screened_rather_than_skipped(self):
        p = make_pipeline()
        p.rt.start_run(("discover", "ingest", "mask"))
        run_until_idle(p.rt.worker(p.stages()))

        body = _papers(p).json()

        assert {r["screening_status"] for r in body["papers"]} == {"not_screened"}
        assert body["counts"]["not_screened"] == 5


class TestLinks:
    def test_a_paper_with_a_doi_and_no_link_links_to_the_doi_resolver(self, screened):
        p, _ = screened

        rows = {r["title"]: r for r in _papers(p).json()["papers"]}

        vouchers = rows["Food vouchers in displacement camps"]
        assert vouchers["url"] == "https://doi.org/10.1000/voucher.2"

    def test_a_paper_keeps_its_own_link_when_it_has_one(self, screened):
        p, _ = screened

        rows = {r["title"]: r for r in _papers(p).json()["papers"]}

        assert rows[CORPUS[0].title]["url"] == "https://example.org/f-1"

    def test_a_paper_with_neither_has_no_link(self, screened):
        p, _ = screened

        rows = {r["title"]: r for r in _papers(p).json()["papers"]}

        assert rows["Cash transfers in stable urban markets"]["url"] is None


class TestCitedInBrief:
    """The expanded row says whether the run's brief cites the paper, by its tag."""

    def test_a_paper_the_runs_brief_cites_is_marked_as_cited(self):
        p, _ = _briefed(cite="P2")

        rows = {r["tag"]: r["is_cited_in_brief"] for r in _papers(p).json()["papers"]}

        assert rows == {"P1": False, "P2": True, "P3": False, "P4": False, "P5": False}

    def test_no_paper_is_cited_before_the_run_has_a_brief(self, screened):
        p, _ = screened

        rows = _papers(p).json()["papers"]

        assert {r["is_cited_in_brief"] for r in rows} == {False}

    def test_one_paper_says_whether_the_brief_cites_it(self):
        p, _ = _briefed(cite="P2")
        paper_id = next(r for r in _papers(p).json()["papers"] if r["tag"] == "P2")["paper_id"]

        body = p.rt.owner.get(f"/api/v1/reviews/{p.rt.review_id}/papers/{paper_id}").json()

        assert body["paper"]["is_cited_in_brief"] is True


def _briefed(*, cite: str) -> tuple[Pipeline, str]:
    """A run that screens, then writes a brief whose first benefit cites `cite`."""
    p = make_pipeline()
    cited = Passage(
        cite, UUID(int=99), SourceKind.PAPER, "Cash raised diet diversity.", "abstract"
    )
    script = RoleScript(
        planner=[planner_answer()], writer=[writer_answer(cite=cite)], critic=[critic_answer()]
    )
    rig = answer_stage(p.rt, script, passages=(*BRIEF_CORPUS, cited), max_rounds=1)
    p.extra["answer"] = rig.stage
    run_id = p.rt.start_run((*ALL_STAGES, "answer"))
    run_until_idle(p.rt.worker(p.stages()))
    assert p.rt.stored_run(run_id).state is RunState.DONE, p.failure(run_id)
    return p, run_id


class TestCriterionAnswers:
    """The expanded row shows each criterion's answer, with its quote."""

    def test_each_criterion_is_answered_with_a_quote_from_the_masked_text(self):
        p = _screen(_with_answers)
        first = _papers(p).json()["papers"][0]

        detail = p.rt.owner.get(
            f"/api/v1/reviews/{p.rt.review_id}/papers/{first['paper_id']}"
        ).json()

        answers = detail["paper"]["decision"]["criteria"]
        assert [a["id"] for a in answers] == ["I1", "I2", "E1"]
        assert {a["answer"] for a in answers} <= {"yes", "partly", "no", "unclear"}
        quotes = [a["quote"] for a in answers if a["quote"]]
        assert quotes
        assert all(q in detail["masked_text"] for q in quotes)
        assert PLACE not in str(answers)

    def test_a_quote_the_model_made_up_is_refused_and_the_paper_is_asked_again(self):
        p = _screen(lambda request: _with_answers(request, quote_from="words nobody wrote"))

        rows = _papers(p).json()["papers"]

        decided = [r["decision"] for r in rows if r["decision"]]
        assert len(decided) == 4
        assert all(d["is_unreadable"] and d["criteria"] == [] for d in decided)
        assert len(p.model.requests) == 2 * len(decided)

    def test_answers_that_disagree_with_the_criteria_met_are_refused(self):
        def contradicting(request: ChatRequest) -> dict[str, Any]:
            answer = _with_answers(request)
            for item in answer["answers"]:
                item["answer"], item["quote"] = "no", None
            return answer

        p = _screen(contradicting)

        decided = [r["decision"] for r in _papers(p).json()["papers"] if r["decision"]]
        assert [d for d in decided if d["label"] == "include"] == []

    def test_an_answer_that_skips_a_criterion_is_refused_and_asked_again(self):
        def skipping(request: ChatRequest) -> dict[str, Any]:
            answer = _with_answers(request)
            answer["answers"] = answer["answers"][:-1]
            return answer

        p = _screen(skipping)

        decided = [r["decision"] for r in _papers(p).json()["papers"] if r["decision"]]
        assert len(decided) == 4
        assert all(d["is_unreadable"] for d in decided)
        assert len(p.model.requests) == 2 * len(decided)

    def test_an_answer_without_per_criterion_answers_is_refused(self):
        def unanswered(request: ChatRequest) -> dict[str, Any]:
            answer = _with_answers(request)
            del answer["answers"]
            return answer

        p = _screen(unanswered)

        decided = [r["decision"] for r in _papers(p).json()["papers"] if r["decision"]]
        assert decided
        assert all(d["is_unreadable"] for d in decided)

    def test_the_answers_are_stored_sealed(self):
        p = _screen(_with_answers)

        stored = asyncio.run(_stored_decisions(p))

        assert stored
        for decision in stored:
            assert decision.answers_sealed is not None
            # JSON keys, not a criterion id: a two-byte id turns up in random
            # ciphertext about once in a hundred runs.
            assert b'"answer"' not in decision.answers_sealed
            assert b'"quote"' not in decision.answers_sealed

    def test_deleting_the_project_makes_the_answers_unreadable(self):
        p = _screen(_with_answers)
        stored = asyncio.run(_stored_decisions(p))
        delete = send(p.rt.owner, "DELETE", f"{PROJECTS}/{p.rt.project_id}")
        assert delete.status_code == 204

        with pytest.raises(KeyDestroyedError):
            asyncio.run(_open(p, stored[0]))


async def _stored_decisions(p: Pipeline) -> list[Any]:
    async with p.rt.harness.providers.storage.unit_of_work() as uow:
        runs = await uow.runs.list_for_review(UUID(p.rt.review_id))
        return await uow.screening.list_for_run(runs[0].id)


async def _open(p: Pipeline, stored: Any) -> Any:
    async with p.rt.harness.providers.storage.unit_of_work() as uow:
        return await p.store.open_decision(uow, stored)
