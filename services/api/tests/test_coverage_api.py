"""Requirements for the coverage estimate on the screening table (UI_UX_SPEC §6.8, ADR 0021).

The papers list carries the estimate for the labels it shows. The screen stage records the
estimate it worked out in the run's manifest, so a reviewer can see later what it was.
"""

from __future__ import annotations

from typing import Any

from app.domain.inference import ChatRequest
from app.domain.paper import PaperRecord
from tests.pipeline_support import (
    ALL_STAGES,
    CORPUS,
    CorpusSource,
    FailingSource,
    Pipeline,
    answer,
    make_pipeline,
    paper_text_in,
    reasonable_answer,
)
from tests.project_support import send
from tests.run_support import run_until_idle


def _paper(n: int) -> PaperRecord:
    return PaperRecord(
        source_id="any",
        external_id=f"p-{n}",
        title=f"Cash transfers and malnutrition among displaced families, study {n}",
        year=2020,
        doi=f"10.1000/displaced.{n}",
        abstract=f"Cash for displaced households; malnutrition fell in study {n}.",
    )


# Relevant to the plan, but in a stable setting: excluded unless rules say otherwise.
URBAN = PaperRecord(
    source_id="any",
    external_id="urban",
    title="Cash transfers in stable urban markets",
    year=2020,
    doi="10.1000/urban",
    abstract="Cash and prices in a non-crisis city; malnutrition was measured.",
)

RELEVANT = tuple(_paper(n) for n in range(1, 13))
# Papers 1 to 10 in OpenAlex and 3 to 12 in Europe PMC: 8 found by both, 12 in all.
OPENALEX = (*RELEVANT[:10], URBAN)
EUROPEPMC = (*RELEVANT[2:], URBAN)


def _partly_urban(request: ChatRequest) -> dict[str, Any]:
    """`reasonable_answer`, except that the urban paper only partly meets E1."""
    if "stable urban" in paper_text_in(request):
        verdicts = {"I1": "no", "I2": "yes", "E1": "partly"}
        return {
            **answer("exclude", "low", ["I2", "E1"]),
            "answers": [{"id": c, "answer": v, "quote": None} for c, v in verdicts.items()],
        }
    return reasonable_answer(request)


def _run(
    sources: list[Any] | None = None, stages: tuple[str, ...] = ALL_STAGES
) -> tuple[Pipeline, str]:
    p = make_pipeline(
        sources=sources
        if sources is not None
        else [CorpusSource("openalex", OPENALEX), CorpusSource("europepmc", EUROPEPMC)],
        script=_partly_urban,
    )
    run_id = p.rt.start_run(stages)
    run_until_idle(p.rt.worker(p.stages()))
    assert p.failure(run_id) is None
    return p, run_id


def _coverage(p: Pipeline) -> dict[str, Any]:
    response = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers")
    assert response.status_code == 200, response.text
    coverage: dict[str, Any] = response.json()["coverage"]
    return coverage


class TestOnTheScreeningTable:
    def test_the_papers_list_carries_the_estimate_as_a_range(self):
        p, _ = _run()

        coverage = _coverage(p)

        assert coverage["no_estimate_because"] is None
        assert coverage["interval"] == {
            "sources": ["europepmc", "openalex"],
            "estimated_total": 12,
            "low_percent": 70,
            "high_percent": 100,
        }

    def test_the_estimate_names_its_method_and_the_counts_it_used(self):
        p, _ = _run()

        coverage = _coverage(p)

        assert (coverage["method"], coverage["method_version"]) == ("chapman-pairs", 1)
        assert coverage["relevant_found"] == 12
        assert coverage["searched_sources"] == ["europepmc", "openalex"]
        assert coverage["overlaps"] == [
            {
                "sources": ["europepmc", "openalex"],
                "first_found": 10,
                "second_found": 10,
                "both_found": 8,
            }
        ]

    def test_the_estimate_is_the_same_on_every_page_and_filter(self):
        p, _ = _run()
        path = f"/api/v1/reviews/{p.rt.review_id}/papers"

        filtered = p.rt.viewer.get(f"{path}?label=exclude&page=2").json()["coverage"]

        assert filtered == _coverage(p)

    def test_the_model_s_labels_are_counted_when_no_rules_set_them(self):
        p, _ = _run()

        assert _coverage(p)["rules_version"] is None

    def test_the_estimate_follows_the_label_rules_the_table_shows(self):
        p, _ = _run()
        rules = {"possibly_min_met": 1, "unclear_counts_as": "not_met"}
        applied = send(
            p.rt.owner, "PUT", f"/api/v1/reviews/{p.rt.review_id}/label-rules", rules
        )
        assert applied.status_code == 200, applied.text

        coverage = _coverage(p)

        # The urban paper, found by both sources, is now Possibly relevant.
        assert coverage["rules_version"] == 1
        assert coverage["relevant_found"] == 13
        assert coverage["overlaps"][0]["both_found"] == 9


class TestNoEstimate:
    def test_one_source_gives_no_estimate_and_says_why(self):
        p, _ = _run([CorpusSource("openalex", OPENALEX)])

        coverage = _coverage(p)

        assert coverage["interval"] is None
        assert coverage["no_estimate_because"] == "one_source"

    def test_a_source_that_failed_is_not_counted_as_searched(self):
        p, _ = _run([CorpusSource("openalex", OPENALEX), FailingSource("europepmc")])

        coverage = _coverage(p)

        assert coverage["searched_sources"] == ["openalex"]
        assert coverage["no_estimate_because"] == "one_source"

    def test_sources_that_share_too_few_relevant_papers_give_no_estimate(self):
        p, _ = _run(
            [
                CorpusSource("openalex", CORPUS),
                CorpusSource("europepmc", RELEVANT[:3]),
            ]
        )

        assert _coverage(p)["no_estimate_because"] == "too_few_overlaps"

    def test_before_screening_has_finished_there_is_no_estimate(self):
        p, _ = _run(stages=("discover", "ingest", "mask"))

        coverage = _coverage(p)

        assert coverage["interval"] is None
        assert coverage["no_estimate_because"] == "screening_unfinished"


class TestRecordedInTheManifest:
    def test_the_screen_stage_records_the_estimate_it_worked_out(self):
        p, run_id = _run()

        recorded = p.summary(run_id, "screen")["manifest"]["coverage"]

        assert recorded["method"] == "chapman-pairs"
        assert recorded["method_version"] == 1
        assert recorded["interval"]["low_percent"] == 70
        assert recorded["overlaps"][0]["both_found"] == 8

    def test_the_manifest_route_shows_the_recorded_estimate(self):
        p, run_id = _run()

        body = p.rt.viewer.get(f"/api/v1/runs/{run_id}/manifest").json()

        screen = next(s for s in body["stage_facts"] if s["stage"] == "screen")
        assert screen["facts"]["coverage"]["relevant_found"] == 12

    def test_the_recorded_estimate_stays_when_the_rules_change_later(self):
        p, run_id = _run()
        rules = {"possibly_min_met": 1, "unclear_counts_as": "not_met"}
        send(p.rt.owner, "PUT", f"/api/v1/reviews/{p.rt.review_id}/label-rules", rules)

        body = p.rt.viewer.get(f"/api/v1/runs/{run_id}/manifest").json()

        screen = next(s for s in body["stage_facts"] if s["stage"] == "screen")
        assert screen["facts"]["coverage"]["relevant_found"] == 12
