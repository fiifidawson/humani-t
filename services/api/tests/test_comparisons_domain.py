"""Comparing two versions of a brief, or two runs of one review, as rules with no I/O.

A comparison reads from the older to the newer and says only what differs; it never
judges which is better. Each test builds two valid sides and perturbs one thing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.domain.briefs import (
    BriefBody,
    Citation,
    Claim,
    ClaimSupport,
    EvidenceStrength,
    Option,
    SourceKind,
)
from app.domain.comparisons import (
    BriefSource,
    RunPapers,
    ScreenedPaper,
    compare_briefs,
    compare_discovery,
    compare_screening,
    compare_setup,
)
from app.domain.manifest import RunManifest, StageSummary, assemble_manifest
from app.domain.runs import JsonValue, Run, RunState
from app.domain.screening import ScreeningLabel

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)

CASH, FOOD, VOUCHERS = "Cash transfers", "In-kind food", "Vouchers"


def _claim(text: str, tag: str = "P1") -> Claim:
    return Claim(text, ClaimSupport.EVIDENCE, (Citation(tag, SourceKind.PAPER),))


def _body(*options: tuple[str, EvidenceStrength]) -> BriefBody:
    return BriefBody(
        bottom_line=(_claim("One."), _claim("Two.")),
        options=tuple(
            Option(name, f"{name}, described.", strength, (_claim("A benefit"),), ())
            for name, strength in options
        ),
        trade_offs=(),
        conflicts=(),
        considerations=(),
        gaps=(),
    )


def _source(n: int, tag: str | None = None) -> BriefSource:
    return BriefSource(UUID(int=n), SourceKind.PAPER, tag or f"P{n}", f"Paper {n}")


BEFORE = _body((CASH, EvidenceStrength.WEAK), (FOOD, EvidenceStrength.MODERATE))


class TestCompareBriefs:
    def test_two_identical_briefs_differ_in_nothing(self):
        compared = compare_briefs(
            BEFORE, BEFORE, sources_before=[_source(1)], sources_after=[_source(1)]
        )

        assert compared.options_added == ()
        assert compared.options_removed == ()
        assert compared.strength_changes == ()
        assert compared.sources_added == ()
        assert compared.sources_removed == ()

    def test_an_option_only_in_the_newer_brief_was_added_and_one_only_in_the_older_removed(
        self,
    ):
        after = _body((CASH, EvidenceStrength.WEAK), (VOUCHERS, EvidenceStrength.WEAK))

        compared = compare_briefs(BEFORE, after, sources_before=[], sources_after=[])

        assert compared.options_added == (VOUCHERS,)
        assert compared.options_removed == (FOOD,)

    def test_a_strength_change_names_the_option_and_both_strengths(self):
        after = _body((CASH, EvidenceStrength.MODERATE), (FOOD, EvidenceStrength.MODERATE))

        compared = compare_briefs(BEFORE, after, sources_before=[], sources_after=[])

        [change] = compared.strength_changes
        assert (change.option, change.before, change.after) == (
            CASH,
            EvidenceStrength.WEAK,
            EvidenceStrength.MODERATE,
        )

    def test_options_match_whatever_their_case_and_spacing(self):
        after = _body(
            ("cash  TRANSFERS", EvidenceStrength.WEAK), (FOOD, EvidenceStrength.MODERATE)
        )

        compared = compare_briefs(BEFORE, after, sources_before=[], sources_after=[])

        assert (compared.options_added, compared.options_removed) == ((), ())

    def test_sources_are_matched_by_paper_not_by_tag(self):
        """A tag is a paper's place in one run's list; the same paper can move."""
        compared = compare_briefs(
            BEFORE,
            BEFORE,
            sources_before=[_source(1, "P1"), _source(2, "P2")],
            sources_after=[_source(1, "P7"), _source(3, "P2")],
        )

        assert compared.sources_added == (_source(3, "P2"),)
        assert compared.sources_removed == (_source(2, "P2"),)


def _run(stages: tuple[str, ...]) -> Run:
    return Run(
        id=uuid.uuid4(),
        review_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        stages=stages,
        state=RunState.DONE,
        created_at=NOW,
        created_by=uuid.uuid4(),
        queued_at=NOW,
        stages_finished=len(stages),
        started_at=NOW,
        finished_at=NOW,
    )


def _manifest(facts: Mapping[str, Mapping[str, JsonValue]]) -> RunManifest:
    return assemble_manifest(
        _run(tuple(facts)),
        [StageSummary(stage, 1.0, {"manifest": dict(f)}) for stage, f in facts.items()],
    )


SETUP: dict[str, dict[str, JsonValue]] = {
    "discover": {
        "vocabulary_version": 2,
        "sources": ["openalex"],
        "queries": {"openalex": '("cash")'},
        "limit_per_source": 25,
    },
    "screen": {
        "model_id": "llama3.1:8b",
        "prompt_version": "screen-3",
        "criteria_version": 2,
        "is_masked": True,
    },
}


def _changed(facts: dict[str, dict[str, JsonValue]], stage: str, **values: JsonValue):
    return {**facts, stage: {**facts[stage], **values}}


class TestCompareSetup:
    def test_the_same_setup_shows_side_by_side_and_changes_nothing(self):
        compared = compare_setup(_manifest(SETUP), _manifest(SETUP))

        assert (compared.vocabulary_versions.before, compared.vocabulary_versions.after) == (
            [2],
            [2],
        )
        assert not compared.vocabulary_versions.is_changed
        assert compared.model_ids.after == ["llama3.1:8b"]
        assert compared.queries_added == ()
        assert compared.queries_removed == ()
        assert compared.settings_changed == ()

    def test_a_new_vocabulary_version_and_source_show_as_changed(self):
        after = _changed(
            SETUP,
            "discover",
            vocabulary_version=3,
            sources=["openalex", "arxiv"],
            queries={"openalex": '("cash")', "arxiv": "all:cash"},
        )

        compared = compare_setup(_manifest(SETUP), _manifest(after))

        assert compared.vocabulary_versions.is_changed
        assert compared.sources.after == ["openalex", "arxiv"]
        assert [(q.source, q.query) for q in compared.queries_added] == [("arxiv", "all:cash")]
        assert compared.queries_removed == ()

    def test_a_rewritten_query_is_one_removed_and_one_added(self):
        after = _changed(SETUP, "discover", queries={"openalex": '("cash" OR "voucher")'})

        compared = compare_setup(_manifest(SETUP), _manifest(after))

        assert [q.query for q in compared.queries_removed] == ['("cash")']
        assert [q.query for q in compared.queries_added] == ['("cash" OR "voucher")']

    def test_prompt_versions_are_listed_for_both_runs(self):
        after = _changed(SETUP, "screen", prompt_version="screen-4")

        compared = compare_setup(_manifest(SETUP), _manifest(after))

        [prompt] = compared.prompt_versions
        assert (prompt.name, prompt.before, prompt.after) == ("screen", "screen-3", "screen-4")
        assert prompt.is_changed

    def test_only_the_settings_that_changed_are_listed(self):
        after = _changed(
            _changed(SETUP, "discover", limit_per_source=50), "screen", is_masked=False
        )

        compared = compare_setup(_manifest(SETUP), _manifest(after))

        assert [(c.name, c.before, c.after) for c in compared.settings_changed] == [
            ("masking", "on", "off"),
            ("discover.limit_per_source", 25, 50),
        ]

    def test_a_stage_only_one_run_finished_is_not_a_changed_setting(self):
        """A run that redid only the brief did not change how papers were found."""
        brief_only = {"answer": {"model_ids": {"writer": "qwen3.5:9b"}, "max_rounds": 3}}

        compared = compare_setup(_manifest(SETUP), _manifest(brief_only))

        assert all(
            not c.name.startswith(("discover.", "answer.")) for c in compared.settings_changed
        )


class TestManifestRecords:
    def test_a_run_whose_stages_recorded_nothing_has_no_record_of_its_setup(self):
        assert not _manifest({"discover": {}}).has_record
        assert not assemble_manifest(_run(("discover",)), []).has_record

    def test_a_run_with_any_recorded_fact_has_a_record(self):
        assert _manifest(SETUP).has_record

    def test_the_models_a_brief_names_per_role_are_among_the_runs_models(self):
        manifest = _manifest(
            {"answer": {"model_ids": {"writer": "qwen3.5:9b", "critic": "llama3.1:8b"}}}
        )

        assert manifest.model_ids == ("llama3.1:8b", "qwen3.5:9b")


def _paper(n: int, label: ScreeningLabel | None = None, reason: str | None = None):
    return ScreenedPaper(UUID(int=n), f"Paper {n}", label, reason)


INCLUDE, EXCLUDE, UNCERTAIN = (
    ScreeningLabel.INCLUDE,
    ScreeningLabel.EXCLUDE,
    ScreeningLabel.UNCERTAIN,
)


class TestCompareDiscovery:
    def test_papers_found_by_one_run_only_are_listed_on_its_side(self):
        before = RunPapers(identified=4, papers=(_paper(1), _paper(2), _paper(3)))
        after = RunPapers(identified=6, papers=(_paper(2), _paper(3), _paper(4), _paper(5)))

        compared = compare_discovery(before, after)

        assert (compared.before.identified, compared.after.identified) == (4, 6)
        assert (
            compared.before.after_deduplication,
            compared.after.after_deduplication,
        ) == (3, 4)
        assert [p.paper_id for p in compared.only_before] == [UUID(int=1)]
        assert [p.paper_id for p in compared.only_after] == [UUID(int=4), UUID(int=5)]
        assert compared.in_both == 2


class TestCompareScreening:
    def test_label_counts_are_side_by_side_with_unscreened_papers_counted(self):
        before = RunPapers(4, (_paper(1, INCLUDE), _paper(2, EXCLUDE), _paper(3)))
        after = RunPapers(4, (_paper(1, INCLUDE), _paper(2, INCLUDE), _paper(3, UNCERTAIN)))

        compared = compare_screening(before, after)

        assert compared.before == {"include": 1, "exclude": 1, "uncertain": 0, "unscreened": 1}
        assert compared.after == {"include": 2, "exclude": 0, "uncertain": 1, "unscreened": 0}

    def test_a_paper_whose_label_changed_is_listed_with_the_newer_runs_reason(self):
        before = RunPapers(2, (_paper(1, INCLUDE, "Old."), _paper(2, EXCLUDE, "Old.")))
        after = RunPapers(2, (_paper(1, INCLUDE, "Same."), _paper(2, INCLUDE, "Now fits.")))

        [change] = compare_screening(before, after).changed

        assert (change.paper_id, change.before, change.after) == (UUID(int=2), EXCLUDE, INCLUDE)
        assert (change.title, change.reason) == ("Paper 2", "Now fits.")

    def test_a_paper_screened_by_one_run_only_did_not_change_label(self):
        before = RunPapers(2, (_paper(1), _paper(2, EXCLUDE)))
        after = RunPapers(2, (_paper(1, INCLUDE), _paper(3, INCLUDE)))

        assert compare_screening(before, after).changed == ()
