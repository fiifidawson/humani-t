"""Downloads: the screening dataset, included papers, PRISMA, the manifest and the brief.

Sources: UI_UX_SPEC §6.8 ("Downloads: dataset.csv, dataset.jsonl, prisma.md"),
§6.9 ("Download as Markdown, PDF and JSON"; the caveat on every printed page),
§8.7, §15 P2 (`…/exports/{name}`); architecture §5.3 (`Cache-Control:
no-store`), §5.5 (non-members get 404, audit ids only); OWASP CSV injection.

The papers come from the real pipeline stages over a fake corpus
(`tests/pipeline_support.py`); the brief from the real answer stage with a
scripted model (`tests/brief_support.py`).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

import pytest

from app.domain.briefs import CAVEAT
from app.domain.inference import ChatRequest
from tests.brief_support import MASKED_PLACE, write_brief
from tests.pipeline_support import ALL_STAGES, PLACE, Pipeline, make_pipeline
from tests.project_support import project_events
from tests.run_support import run_until_idle


@pytest.fixture
def p() -> Pipeline:
    pipeline = make_pipeline()
    run_id = pipeline.rt.start_run(list(ALL_STAGES))
    run_until_idle(pipeline.rt.worker(pipeline.stages()))
    assert pipeline.failure(run_id) is None
    return pipeline


def export_path(p: Pipeline, name: str, run_id: str | None = None) -> str:
    query = "" if run_id is None else f"?run_id={run_id}"
    return f"/api/v1/reviews/{p.rt.review_id}/exports/{name}{query}"


def download(p: Pipeline, name: str) -> Any:
    response = p.rt.viewer.get(export_path(p, name))
    assert response.status_code == 200, response.text
    return response


def csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


class TestDataset:
    def test_the_csv_has_one_row_per_paper_with_its_decision(self, p):
        response = download(p, "dataset.csv")

        rows = csv_rows(response.text)
        assert response.headers["content-type"].startswith("text/csv")
        papers = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers").json()
        assert len(rows) == papers["total"]
        first = rows[0]
        assert first["tag"].startswith("P")
        assert first["label"] in {"include", "exclude", "uncertain", ""}
        assert {"title", "doi", "rationale", "model_id", "prompt_version"} <= set(first)

    def test_the_csv_lists_papers_in_the_order_the_screen_shows(self, p):
        rows = csv_rows(download(p, "dataset.csv").text)
        papers = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers").json()

        assert [r["tag"] for r in rows][: len(papers["papers"])] == [
            row["tag"] for row in papers["papers"]
        ]

    def test_a_cell_a_spreadsheet_would_run_is_neutralised(self, p):
        def formula_answer(request: ChatRequest) -> dict[str, Any]:
            return {
                "label": "include",
                "score": 0.9,
                "priority": "high",
                "rationale": '=HYPERLINK("http://evil.example","click")',
                "criteria_matched": ["I1", "I2"],
            }

        pipeline = make_pipeline(script=formula_answer)
        pipeline.rt.start_run(list(ALL_STAGES))
        run_until_idle(pipeline.rt.worker(pipeline.stages()))

        rows = csv_rows(download(pipeline, "dataset.csv").text)

        rationales = [r["rationale"] for r in rows if r["rationale"]]
        assert rationales
        assert all(r.startswith("'=") for r in rationales)

    def test_the_jsonl_has_one_paper_per_line_as_the_paper_list_shows_it(self, p):
        lines = download(p, "dataset.jsonl").text.splitlines()
        papers = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers").json()

        first = json.loads(lines[0])
        assert len(lines) == papers["total"]
        assert first == papers["papers"][0]

    def test_no_raw_kept_text_is_exported(self, p):
        for name in ("dataset.csv", "dataset.jsonl", "included.ris"):
            assert "Unconditional cash for displaced households" not in download(p, name).text


class TestIncludedPapers:
    def test_the_ris_file_lists_only_included_papers(self, p):
        text = download(p, "included.ris").text
        rows = csv_rows(download(p, "dataset.csv").text)

        included = [r for r in rows if r["label"] == "include"]
        assert text.count("TY  - JOUR") == len(included) > 0
        assert text.count("ER  - ") == len(included)
        assert "TI  - Cash transfers and acute malnutrition among displaced children" in text
        assert "DO  - 10.1000/cash.1" in text
        assert "Cash transfers in stable urban markets" not in text


class TestPrismaAndManifest:
    def test_prisma_is_a_markdown_table_with_the_screens_counts(self, p):
        text = download(p, "prisma.md").text
        counts = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/prisma").json()

        assert text.startswith("# PRISMA")
        assert f"| Included | {counts['included']} |" in text
        assert f"| Records after duplicates removed | {counts['after_deduplication']} |" in text

    def test_the_manifest_is_the_manifest_route_as_a_file(self, p):
        body = download(p, "manifest.json").json()
        run_id = body["run_id"]

        assert body == p.rt.viewer.get(f"/api/v1/runs/{run_id}/manifest").json()


class TestBrief:
    @pytest.fixture
    def with_brief(self, p):
        write_brief(p.rt)
        return p

    def test_the_brief_json_is_the_brief_route_as_a_file(self, with_brief):
        p = with_brief
        body = download(p, "brief.json").json()

        assert body == p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/brief").json()

    def test_the_markdown_brief_carries_the_caveat_options_and_sources(self, with_brief):
        text = download(with_brief, "brief.md").text

        assert CAVEAT in text
        assert "## Options" in text
        assert "Cash transfers" in text
        assert "[P0001]" in text
        assert "reasoning, not a source" in text
        assert MASKED_PLACE in text
        assert PLACE not in text

    def test_the_print_brief_repeats_the_caveat_on_every_page_and_runs_no_script(
        self, with_brief
    ):
        text = download(with_brief, "brief.html").text

        assert text.startswith("<!doctype html>")
        assert "@page" in text
        assert "position: fixed" in text
        assert CAVEAT in text
        assert "<script" not in text.lower()

    def test_the_print_brief_escapes_the_models_text(self, p):
        from tests.brief_support import (
            RoleScript,
            answer_stage,
            critic_answer,
            planner_answer,
            writer_answer,
        )

        hostile = writer_answer()
        hostile["gaps"] = ["<img src=x onerror=alert(1)>"]
        script = RoleScript(
            planner=[planner_answer()], writer=[hostile], critic=[critic_answer()]
        )
        write_brief(p.rt, answer_stage(p.rt, script))

        text = download(p, "brief.html").text

        assert "<img" not in text
        assert "&lt;img src=x onerror=alert(1)&gt;" in text

    def test_no_brief_yet_is_404(self, p):
        assert p.rt.viewer.get(export_path(p, "brief.md")).status_code == 404


class TestEveryDownload:
    @pytest.mark.parametrize(
        "name", ["dataset.csv", "dataset.jsonl", "included.ris", "prisma.md", "manifest.json"]
    )
    def test_it_is_an_attachment_never_cached(self, p, name: str):
        response = download(p, name)

        disposition = response.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="Humani-T-run-')
        assert disposition.endswith(f'-{name}"')
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_each_download_is_audited_with_ids_only(self, p):
        download(p, "dataset.csv")

        events = [
            e
            for e in project_events(p.rt.harness, p.rt.project_id)
            if e.entry.action == "export.downloaded"
        ]
        assert len(events) == 1
        entry = events[0].entry
        assert (entry.target_type, entry.target_id) == ("review", p.rt.review_id)
        assert entry.actor_id is not None

    def test_an_unknown_export_is_404(self, p):
        assert p.rt.viewer.get(export_path(p, "passwords.txt")).status_code == 404

    def test_a_run_of_another_review_is_404(self, p):
        response = p.rt.viewer.get(export_path(p, "dataset.csv", str(uuid.uuid4())))

        assert response.status_code == 404

    def test_a_non_member_gets_the_same_404_as_for_a_missing_review(self, p):
        existing = p.rt.outsider.get(export_path(p, "dataset.csv"))
        missing = p.rt.outsider.get(f"/api/v1/reviews/{uuid.uuid4()}/exports/dataset.csv")

        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()
