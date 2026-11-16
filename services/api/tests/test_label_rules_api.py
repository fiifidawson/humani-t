"""Requirements for editing a review's label rules (UI_UX_SPEC §6.8, ADR 0013).

Rules relabel papers from the per-criterion answers the model already gave: no model call,
and the model's decisions stay as they were. Owners and reviewers may preview and apply;
every member may read them. The papers list, its counts, PRISMA and the downloads all
show the label the latest rules give.
"""

from __future__ import annotations

import asyncio
import csv
import io
from typing import Any
from uuid import UUID

import pytest

from app.domain.inference import ChatRequest
from tests.pipeline_support import (
    ALL_STAGES,
    Pipeline,
    answer,
    make_pipeline,
    paper_text_in,
    reasonable_answer,
)
from tests.project_support import project_events, send
from tests.run_support import run_until_idle

_MISSING = "00000000-0000-4000-8000-000000000000"
DEFAULT = {"possibly_min_met": 1, "unclear_counts_as": "not_met"}
STRICT = {"possibly_min_met": 2, "unclear_counts_as": "not_met"}


def _answers(verdicts: dict[str, str]) -> list[dict[str, Any]]:
    return [{"id": c, "answer": v, "quote": None} for c, v in verdicts.items()]


def scripted(request: ChatRequest) -> dict[str, Any]:
    """`reasonable_answer`, except two papers whose answers rules read differently.

    The urban paper meets E1 only partly, which rules do not count as an exclusion. The
    mobile-money paper's answer cannot be read at all, so no rule can reach it.
    """
    text = paper_text_in(request)
    if "stable urban" in text:
        return {
            **answer("exclude", "low", ["I2", "E1"]),
            "answers": _answers({"I1": "no", "I2": "yes", "E1": "partly"}),
        }
    if "mobile money" in text.lower():
        return answer("include", "high", ["I1", "I2"])
    return reasonable_answer(request)


@pytest.fixture
def p() -> Pipeline:
    pipeline = make_pipeline(script=scripted)
    run_id = pipeline.rt.start_run(list(ALL_STAGES))
    run_until_idle(pipeline.rt.worker(pipeline.stages()))
    assert pipeline.failure(run_id) is None
    return pipeline


def rules_path(p: Pipeline, suffix: str = "") -> str:
    return f"/api/v1/reviews/{p.rt.review_id}/label-rules{suffix}"


def preview(p: Pipeline, rules: dict[str, Any], browser: Any = None) -> Any:
    return send(browser or p.rt.owner, "POST", rules_path(p, "/preview"), rules)


def apply(p: Pipeline, rules: dict[str, Any], browser: Any = None) -> Any:
    return send(browser or p.rt.owner, "PUT", rules_path(p), rules)


def papers(p: Pipeline, query: str = "") -> dict[str, Any]:
    response = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers{query}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def by_title(body: dict[str, Any], words: str) -> dict[str, Any]:
    return next(row for row in body["papers"] if words in row["title"])


class TestReadingRules:
    def test_a_review_without_rules_has_none_and_no_versions(self, p):
        body = p.rt.viewer.get(rules_path(p)).json()

        assert body == {"current": None, "versions": []}

    def test_every_member_may_read_the_rules(self, p):
        for browser in (p.rt.owner, p.rt.reviewer, p.rt.viewer):
            assert browser.get(rules_path(p)).status_code == 200

    def test_a_non_member_gets_the_same_404_as_for_a_missing_review(self, p):
        theirs = p.rt.outsider.get(rules_path(p))
        missing = p.rt.outsider.get(f"/api/v1/reviews/{_MISSING}/label-rules")

        assert theirs.status_code == 404
        assert theirs.json() == missing.json()


class TestPreview:
    def test_preview_counts_the_papers_that_would_move_between_tiers(self, p):
        body = preview(p, DEFAULT).json()

        assert body["moves"] == [
            {"from_tier": "relevant", "to_tier": "highly_relevant", "count": 1},
            {"from_tier": "excluded", "to_tier": "possibly_relevant", "count": 1},
        ]
        assert body["moved"] == 2

    def test_preview_counts_the_papers_the_rules_cannot_reach(self, p):
        assert preview(p, DEFAULT).json()["untouchable"] == 1

    def test_stricter_rules_move_fewer_papers(self, p):
        body = preview(p, STRICT).json()

        assert body["moves"] == [
            {"from_tier": "relevant", "to_tier": "highly_relevant", "count": 1}
        ]

    def test_preview_changes_nothing(self, p):
        before = papers(p)

        assert preview(p, DEFAULT).status_code == 200

        assert papers(p) == before
        assert p.rt.viewer.get(rules_path(p)).json()["current"] is None

    def test_preview_and_apply_make_no_model_call(self, p):
        calls = len(p.model.requests)

        preview(p, DEFAULT)
        apply(p, DEFAULT)

        assert len(p.model.requests) == calls

    @pytest.mark.parametrize(
        "rules",
        [
            {"possibly_min_met": 0, "unclear_counts_as": "met"},
            {"possibly_min_met": 1, "unclear_counts_as": "maybe"},
            {"possibly_min_met": 1},
        ],
    )
    def test_rules_that_make_no_sense_are_refused(self, p, rules):
        assert preview(p, rules).status_code == 422
        assert apply(p, rules).status_code == 422

    def test_a_review_without_papers_previews_no_moves(self):
        empty = make_pipeline()

        body = preview(empty, DEFAULT).json()

        assert (body["run_id"], body["moves"], body["untouchable"]) == (None, [], 0)


class TestApply:
    def test_apply_stores_a_new_version_with_who_and_how_many_moved(self, p):
        response = apply(p, DEFAULT)

        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 2
        assert body["version"]["version"] == 1
        current = p.rt.viewer.get(rules_path(p)).json()["current"]
        assert current == body["version"]
        assert (current["possibly_min_met"], current["unclear_counts_as"]) == (1, "not_met")
        assert current["papers_moved"] == 2
        assert current["changed_by"]
        assert current["changed_at"]

    def test_each_apply_is_a_new_version_and_older_ones_stay(self, p):
        apply(p, DEFAULT)
        second = apply(p, STRICT).json()

        versions = p.rt.viewer.get(rules_path(p)).json()["versions"]

        assert [v["version"] for v in versions] == [1, 2]
        assert second["version"]["version"] == 2
        assert second["moves"] == [
            {"from_tier": "possibly_relevant", "to_tier": "excluded", "count": 1}
        ]

    def test_apply_changes_the_labels_and_counts_in_the_papers_list(self, p):
        apply(p, DEFAULT)

        body = papers(p)

        assert by_title(body, "vouchers")["decision"]["tier"] == "highly_relevant"
        assert by_title(body, "stable urban")["decision"]["tier"] == "possibly_relevant"
        assert body["counts"]["highly_relevant"] == 2
        assert body["counts"]["relevant"] == 0
        assert body["counts"]["possibly_relevant"] == 1
        assert body["counts"]["excluded"] == 0

    def test_the_papers_list_says_what_set_each_label(self, p):
        before = papers(p)
        apply(p, DEFAULT)
        after = papers(p)

        vouchers = by_title(after, "vouchers")["decision"]
        unreadable = by_title(after, "Mobile money")["decision"]
        assert by_title(before, "vouchers")["decision"]["label_set_by"] == "model"
        assert by_title(before, "vouchers")["decision"]["rules_version"] is None
        assert (vouchers["label_set_by"], vouchers["rules_version"]) == ("rules", 1)
        assert (unreadable["label_set_by"], unreadable["tier"]) == ("model", "uncertain")

    def test_the_tier_filter_follows_the_rules(self, p):
        apply(p, DEFAULT)

        body = papers(p, "?tier=possibly_relevant")

        assert [row["title"] for row in body["papers"]] == [
            "Cash transfers in stable urban markets"
        ]

    def test_a_paper_relabelled_as_included_has_no_exclusion_reasons(self, p):
        apply(p, DEFAULT)

        urban = by_title(papers(p), "stable urban")["decision"]

        assert (urban["label"], urban["exclusion_reasons"]) == ("include", [])

    def test_the_models_answers_and_rationale_are_unchanged(self, p):
        before = by_title(papers(p), "stable urban")["decision"]
        apply(p, DEFAULT)
        after = by_title(papers(p), "stable urban")["decision"]

        assert after["rationale"] == before["rationale"]
        assert after["criteria"] == before["criteria"]
        assert after["model_id"] == before["model_id"]

    def test_apply_is_audited_with_the_version_and_papers_moved(self, p):
        apply(p, DEFAULT)

        events = [
            e
            for e in project_events(p.rt.harness, p.rt.project_id)
            if e.entry.action == "review.label_rules_changed"
        ]

        assert len(events) == 1
        assert events[0].entry.target_id == p.rt.review_id

    @pytest.mark.parametrize("role", ["owner", "reviewer"])
    def test_owners_and_reviewers_may_preview_and_apply(self, p, role):
        browser = getattr(p.rt, role)

        assert preview(p, DEFAULT, browser).status_code == 200
        assert apply(p, DEFAULT, browser).status_code == 200

    def test_a_viewer_may_neither_preview_nor_apply(self, p):
        assert preview(p, DEFAULT, p.rt.viewer).status_code == 403
        assert apply(p, DEFAULT, p.rt.viewer).status_code == 403
        assert p.rt.viewer.get(rules_path(p)).json()["current"] is None

    def test_a_non_member_gets_404_for_preview_and_apply(self, p):
        assert preview(p, DEFAULT, p.rt.outsider).status_code == 404
        assert apply(p, DEFAULT, p.rt.outsider).status_code == 404


class TestRulesReachTheResults:
    def test_prisma_counts_the_labels_the_rules_give(self, p):
        prisma = f"/api/v1/reviews/{p.rt.review_id}/prisma"
        before = p.rt.viewer.get(prisma).json()

        apply(p, DEFAULT)
        after = p.rt.viewer.get(prisma).json()

        assert (before["included"], before["excluded"]) == (2, 1)
        assert (after["included"], after["excluded"]) == (3, 0)
        assert after["excluded_by_reason"] == {}

    def test_prisma_counts_rule_exclusions_by_the_criteria_missed(self, p):
        apply(p, STRICT)

        prisma = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/prisma").json()

        assert prisma["excluded_by_reason"] == {"I1": 1}

    def test_the_dataset_download_carries_the_rules_label_and_what_set_it(self, p):
        apply(p, DEFAULT)

        response = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/exports/dataset.csv")
        rows = list(csv.DictReader(io.StringIO(response.text)))

        urban = next(r for r in rows if "stable urban" in r["title"])
        assert (urban["tier"], urban["label_set_by"], urban["rules_version"]) == (
            "possibly_relevant",
            "rules",
            "1",
        )

    def test_the_included_download_follows_the_rules(self, p):
        apply(p, DEFAULT)

        ris = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/exports/included.ris").text

        assert "Cash transfers in stable urban markets" in ris


def evidence_titles(p: Pipeline) -> set[str]:
    """The titles of the papers a new brief or answer would read as evidence."""
    body = papers(p)
    titles = {row["paper_id"]: row["title"] for row in body["papers"]}

    async def read() -> tuple[Any, ...]:
        async with p.rt.harness.providers.storage.unit_of_work() as uow:
            return await p.evidence.included_passages(
                uow, UUID(p.rt.review_id), UUID(body["run_id"])
            )

    return {titles[str(passage.paper_id)] for passage in asyncio.run(read())}


class TestRulesReachTheEvidence:
    def test_without_rules_the_evidence_is_the_papers_the_model_included(self, p):
        assert "Cash transfers in stable urban markets" not in evidence_titles(p)

    def test_a_brief_or_answer_reads_the_papers_the_rules_include(self, p):
        apply(p, DEFAULT)

        assert "Cash transfers in stable urban markets" in evidence_titles(p)

    def test_a_paper_the_rules_exclude_is_not_evidence(self, p):
        assert any("vouchers" in title for title in evidence_titles(p))
        apply(p, {"possibly_min_met": 3, "unclear_counts_as": "not_met"})

        included = {row["title"] for row in papers(p, "?label=include")["papers"]}
        assert evidence_titles(p) <= included
