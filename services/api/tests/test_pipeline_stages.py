"""Requirements for the discover, ingest, screen and export stages, run by the real worker.

Sources: the stage seam's two rules (`app/runs/base.py`: a stage may run more
than once; everything reported is shown to every member); architecture §3.8
(provenance on every decision) and §3.9 (nothing reaches a model unmasked);
UI_UX_SPEC §5.3.5 and §6.7 (progress per source and one live row per paper),
§5.3.7 (the screening summary); `CLAUDE.md`'s provenance test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from uuid import UUID

import pytest
import structlog

from app.application import screening
from app.domain.discovery import TextKind
from app.domain.encryption import KeyDestroyedError
from app.domain.runs import JsonValue, RunState
from app.domain.screening import (
    UNREADABLE_RATIONALE,
    PassageSourceKind,
    ScreeningLabel,
)
from app.inference.fake import FakePromptGuard
from app.runs.base import Stage, StageContext
from tests.pipeline_support import (
    ALL_STAGES,
    CORPUS,
    MASKED_PLACE,
    PLACE,
    VOCABULARY,
    CorpusSource,
    FailingSource,
    ObservedInference,
    Pipeline,
    accepted_plan,
    answer,
    contains,
    criteria_ids_in,
    make_pipeline,
    model_down,
    paper_text_in,
    reasonable_answer,
    tag_number,
)
from tests.project_support import PROJECTS, project_events, send
from tests.run_support import LEASE, SimulatedCrashError, run_until_idle


def _run(p: Pipeline, stages: tuple[str, ...] = ALL_STAGES) -> str:
    run_id = p.rt.start_run(stages)
    run_until_idle(p.rt.worker(p.stages()))
    return run_id


def _decisions(p: Pipeline, run_id: str) -> list:
    async def read() -> list:
        async with p.rt.harness.providers.storage.unit_of_work() as uow:
            stored = await uow.screening.list_for_run(UUID(run_id))
            return [await p.store.open_decision(uow, d) for d in stored]

    return asyncio.run(read())


def _run_papers(p: Pipeline, run_id: str) -> list:
    async def read() -> list:
        async with p.rt.harness.providers.storage.unit_of_work() as uow:
            run = await uow.runs.get(UUID(run_id))
            assert run is not None
            return await p.store.run_papers(uow, run)

    return asyncio.run(read())


class _CrashAfter:
    """Lets the wrapped stage finish its work, then kills the worker once before it reports."""

    def __init__(self, inner: Stage) -> None:
        self.inner = inner
        self.crashed = False

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        summary = await self.inner.run(context)
        if not self.crashed:
            self.crashed = True
            raise SimulatedCrashError
        return summary


class TestDiscover:
    def test_every_source_is_searched_with_the_accepted_plans_query(self):
        p = make_pipeline()

        run_id = _run(p, ("discover",))

        for source in p.sources:
            (query,) = source.calls
            assert query.vocabulary_version == VOCABULARY.version
            assert set(query.all_terms) == {"cash", "voucher", "mobile money", "malnutrition"}
        assert p.rt.stored_run(run_id).state is RunState.DONE

    def test_duplicates_across_sources_are_merged_and_counted(self):
        p = make_pipeline()

        run_id = _run(p, ("discover",))

        summary = p.summary(run_id, "discover")
        assert summary["identified"] == 6
        assert summary["by_source"] == {"first": 4, "second": 2}
        assert summary["after_deduplication"] == 5
        assert summary["duplicates_removed"] == 1
        merged = _run_papers(p, run_id)
        assert [m.position for m in merged] == [1, 2, 3, 4, 5]
        assert merged[0].found_by == ("first", "second")

    def test_progress_is_reported_once_per_source(self):
        p = make_pipeline()

        run_id = _run(p, ("discover",))

        rows = p.progress(run_id, "discover")
        assert [(r["done"], r["total"]) for r in rows] == [(1, 2), (2, 2)]
        assert rows[0]["item"] == "first · 4 found"

    def test_a_failing_source_is_recorded_and_the_others_carry_on(self):
        down = FailingSource("down")
        p = make_pipeline(sources=[CorpusSource("first", CORPUS), down])

        run_id = _run(p, ("discover",))

        summary = p.summary(run_id, "discover")
        assert summary["failed_sources"] == ["down"]
        assert summary["after_deduplication"] == 4
        assert p.rt.stored_run(run_id).state is RunState.DONE
        assert any("down" in (r["item"] or "") for r in p.progress(run_id, "discover"))

    def test_a_source_failure_never_shows_its_internal_reason(self):
        p = make_pipeline(sources=[CorpusSource("first", CORPUS), FailingSource("down")])

        run_id = _run(p, ("discover",))

        assert not any(contains(e.data, "10.0.0.7") for e in p.rt.events(run_id))

    def test_the_stage_fails_plainly_only_when_every_source_fails(self):
        p = make_pipeline(sources=[FailingSource("a"), FailingSource("b")])

        run_id = _run(p, ("discover",))

        assert p.rt.stored_run(run_id).state is RunState.FAILED
        assert "could be searched" in (p.failure(run_id) or "")
        assert _run_papers(p, run_id) == []

    def test_a_search_that_finds_nothing_stops_the_run_here_and_says_so(self):
        p = make_pipeline(sources=[CorpusSource("first", ()), CorpusSource("second", ())])

        run_id = _run(p)

        assert p.rt.stored_run(run_id).state is RunState.FAILED
        assert p.failure(run_id) == (
            "The search found no papers. Ask for changes to broaden it, then run it again."
        )
        finished = [e.data["stage"] for e in p.rt.events(run_id) if e.name == "stage.finished"]
        # Discover is the first stage here; nothing after it ran.
        assert finished == []
        assert len(p.progress(run_id, "discover")) == 2

    def test_a_search_where_some_sources_find_papers_carries_on(self):
        p = make_pipeline(sources=[CorpusSource("first", CORPUS), CorpusSource("second", ())])

        run_id = _run(p, ("discover",))

        assert p.rt.stored_run(run_id).state is RunState.DONE
        assert p.summary(run_id, "discover")["after_deduplication"] == 4

    def test_no_sources_configured_fails_plainly(self):
        p = make_pipeline(sources=[])

        run_id = _run(p, ("discover",))

        assert "No paper sources" in (p.failure(run_id) or "")

    def test_a_review_without_an_accepted_plan_fails_plainly(self):
        p = make_pipeline(accept_plan=False)

        run_id = _run(p, ("discover",))

        assert "accepted plan" in (p.failure(run_id) or "")

    def test_a_repeated_discover_searches_nothing_again_and_stores_nothing_twice(self):
        p = make_pipeline()
        stages = p.stages()
        crashing = _CrashAfter(stages["discover"])
        stages["discover"] = crashing
        run_id = p.rt.start_run(("discover",))

        with pytest.raises(SimulatedCrashError):
            run_until_idle(p.rt.worker(stages))
        p.rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(p.rt.worker(stages, "worker-b"))

        assert [len(s.calls) for s in p.sources] == [1, 1]
        assert len(_run_papers(p, run_id)) == 5
        assert p.summary(run_id, "discover")["after_deduplication"] == 5

    def test_a_second_run_finds_the_same_papers_under_the_same_ids(self):
        p = make_pipeline()
        first = _run(p, ("discover",))

        second = _run(p, ("discover",))

        assert [m.paper_id for m in _run_papers(p, first)] == [
            m.paper_id for m in _run_papers(p, second)
        ]
        assert p.summary(second, "discover")["new_papers"] == 0

    def test_the_manifest_records_what_was_asked_of_whom(self):
        p = make_pipeline()

        run_id = _run(p, ("discover",))

        manifest = p.summary(run_id, "discover")["manifest"]
        assert manifest["vocabulary_version"] == 3
        assert manifest["plan_version"] == 3
        assert manifest["sources"] == ["first", "second"]
        assert manifest["queries"]["first"] == (
            '("cash" OR "voucher" OR "mobile money") AND ("malnutrition")'
        )

    def test_paper_records_are_stored_only_sealed(self):
        p = make_pipeline()

        _run(p, ("discover",))

        stored = list(p.rt.harness.storage.state.papers.values())
        assert stored
        assert not any(b"Cash transfers" in s.record_sealed for s in stored)
        assert not any(b"10.1000" in s.identity_digest for s in stored)


class TestIngest:
    def test_each_paper_keeps_its_title_and_abstract_for_masking(self):
        p = make_pipeline()
        run_id = _run(p, ("discover", "ingest"))

        async def read() -> list:
            async with p.rt.harness.providers.storage.unit_of_work() as uow:
                return list(
                    await p.store.texts_for_run(uow, UUID(p.rt.project_id), UUID(run_id))
                )

        texts = asyncio.run(read())
        assert len(texts) == 5
        assert texts[0].kind is TextKind.ABSTRACT
        assert texts[0].text.startswith("Cash transfers and acute malnutrition")
        assert PLACE in texts[0].text
        assert [t.kind for t in texts].count(TextKind.TITLE) == 1

    def test_the_summary_says_full_text_is_not_read_yet(self):
        p = make_pipeline()

        run_id = _run(p, ("discover", "ingest"))

        summary = p.summary(run_id, "ingest")
        assert (summary["abstracts"], summary["title_only"], summary["full_texts"]) == (4, 1, 0)
        assert summary["open_access_not_read"] == 1
        assert any("full text" in gap.lower() for gap in summary["gaps"])

    def test_kept_text_is_sealed(self):
        p = make_pipeline()

        _run(p, ("discover", "ingest"))

        texts = p.rt.harness.storage.state.paper_texts.values()
        assert texts
        assert not any(PLACE.encode() in t.text_sealed for t in texts)

    def test_a_second_ingest_keeps_what_the_first_kept(self):
        p = make_pipeline()
        _run(p, ("discover", "ingest"))
        before = dict(p.rt.harness.storage.state.paper_texts)

        run_id = _run(p, ("discover", "ingest"))

        assert p.rt.harness.storage.state.paper_texts == before
        assert p.summary(run_id, "ingest")["already_kept"] == 5

    def test_a_run_without_discover_works_from_the_newest_discovery(self):
        p = make_pipeline()
        first = _run(p, ("discover",))

        second = _run(p, ("ingest",))

        assert p.summary(second, "ingest")["manifest"]["papers_from_run"] == first
        assert [m.paper_id for m in _run_papers(p, second)] == [
            m.paper_id for m in _run_papers(p, first)
        ]

    def test_a_run_without_any_discovery_fails_plainly(self):
        p = make_pipeline()

        run_id = _run(p, ("ingest",))

        assert "Discover" in (p.failure(run_id) or "")


class TestScreen:
    def test_every_paper_with_an_abstract_gets_one_decision_with_its_provenance(self):
        p = make_pipeline()

        run_id = _run(p)

        decisions = {d.paper_id: d for d in _decisions(p, run_id)}
        assert len(decisions) == 4
        for decision in decisions.values():
            assert decision.model_id == "llama3.1:8b"
            assert decision.criteria_version == 3
            assert decision.prompt_version.startswith("screen-")
            assert decision.evidence_kind is TextKind.ABSTRACT
            assert decision.decided_at == p.rt.clock.now

    def test_the_labels_follow_the_models_answers_in_the_plans_criterion_ids(self):
        p = make_pipeline()

        run_id = _run(p)

        tiers = sorted(d.tier.value for d in _decisions(p, run_id))
        assert tiers == ["excluded", "highly_relevant", "highly_relevant", "relevant"]
        excluded = next(d for d in _decisions(p, run_id) if d.label is ScreeningLabel.EXCLUDE)
        assert excluded.criteria_matched == ("I2", "E1")
        assert excluded.exclusion_reasons == ("E1", "I1")

    def test_the_model_only_ever_sees_masked_text(self):
        p = make_pipeline()

        _run(p)

        assert p.model.requests
        for request in p.model.requests:
            for message in request.messages:
                assert PLACE not in message.content
        assert any(MASKED_PLACE in paper_text_in(r) for r in p.model.requests)

    def test_the_prompt_carries_the_accepted_criteria(self):
        p = make_pipeline()

        _run(p)

        system = p.model.requests[0].messages[0].content
        assert "Displaced people or refugees" in system
        assert "Not a humanitarian or crisis setting" in system
        assert criteria_ids_in(p.model.requests[0]) == ["I1", "I2", "E1"]
        assert p.model.requests[0].temperature == 0.0

    def test_a_paper_not_yet_masked_is_never_sent_and_is_counted_as_skipped(self):
        p = make_pipeline()
        p.mask.skip.add("Food vouchers")

        run_id = _run(p)

        assert not any("Food vouchers" in paper_text_in(r) for r in p.model.requests)
        summary = p.summary(run_id, "screen")
        assert summary["skipped"] == {"no_abstract": 1, "not_masked": 1}

    def test_a_title_without_an_abstract_is_skipped_not_guessed(self):
        p = make_pipeline()

        run_id = _run(p)

        assert not any("trial protocol" in paper_text_in(r) for r in p.model.requests)
        assert p.summary(run_id, "screen")["skipped"]["no_abstract"] == 1

    def test_one_progress_row_per_paper_with_its_label_and_a_one_line_reason(self):
        p = make_pipeline()

        run_id = _run(p)

        rows = p.progress(run_id, "screen")
        assert len(rows) == 4
        assert [r["done"] for r in rows] == [1, 2, 3, 4]
        assert {r["total"] for r in rows} == {4}
        labels = {r["item"].split(" · ")[0] for r in rows}
        assert labels == {"HIGH", "MEDIUM", "EXCLUDE"}
        for row in rows:
            assert "\n" not in row["item"]
            assert "Second line" not in row["item"]
            assert PLACE not in row["item"]

    def test_the_summary_counts_each_label_and_records_the_manifest(self):
        p = make_pipeline()

        run_id = _run(p)

        summary = p.summary(run_id, "screen")
        assert summary["papers"] == 5
        assert summary["tiers"] == {
            "highly_relevant": 2,
            "relevant": 1,
            "possibly_relevant": 0,
            "excluded": 1,
            "uncertain": 0,
        }
        assert summary["failed"] == 0
        assert summary["manifest"] == {
            "model_id": "llama3.1:8b",
            "prompt_version": summary["manifest"]["prompt_version"],
            "criteria_version": 3,
            "plan_version": 3,
        }

    def test_screening_runs_at_most_the_configured_number_of_papers_at_once(self):
        two = make_pipeline(concurrency=2)
        one = make_pipeline(concurrency=1)

        _run(two)
        _run(one)

        assert two.model.max_in_flight == 2
        assert one.model.max_in_flight == 1

    def test_a_rerun_skips_papers_already_decided(self):
        p = make_pipeline()
        p.model = ObservedInference(
            reasonable_answer, fail_with=SimulatedCrashError(), fail_after=2
        )
        run_id = p.rt.start_run(ALL_STAGES)
        with pytest.raises(SimulatedCrashError):
            run_until_idle(p.rt.worker(p.stages()))
        decided_before = {d.paper_id for d in _decisions(p, run_id)}
        p.model = ObservedInference(reasonable_answer)
        p.rt.clock.advance(seconds=LEASE.total_seconds() + 1)

        run_until_idle(p.rt.worker(p.stages(), "worker-b"))

        assert len(decided_before) == 2
        assert len(p.model.requests) == 2
        assert len(_decisions(p, run_id)) == 4
        assert p.rt.stored_run(run_id).state is RunState.DONE

    def test_a_malformed_answer_is_retried_once(self):
        calls: dict[str, int] = {}

        def flaky(request):
            text = paper_text_in(request)
            calls[text] = calls.get(text, 0) + 1
            if calls[text] == 1:
                return {"label": "maybe"}
            return reasonable_answer(request)

        p = make_pipeline(script=flaky)

        run_id = _run(p)

        assert len(p.model.requests) == 8
        assert not any(d.is_unreadable for d in _decisions(p, run_id))

    def test_an_answer_unreadable_twice_is_recorded_as_uncertain_with_the_reason(self):
        p = make_pipeline(script=lambda request: {"label": "maybe"})

        run_id = _run(p)

        decisions = _decisions(p, run_id)
        assert len(decisions) == 4
        assert all(d.label is ScreeningLabel.UNCERTAIN and d.is_unreadable for d in decisions)
        assert all(d.rationale == UNREADABLE_RATIONALE for d in decisions)
        assert len(p.model.requests) == 8
        assert p.summary(run_id, "screen")["tiers"]["uncertain"] == 4

    def test_the_answer_format_asks_for_exactly_one_answer_per_criterion(self):
        p = make_pipeline()

        _run(p)

        schema = p.model.requests[0].output_schema
        criteria = criteria_ids_in(p.model.requests[0])
        assert "answers" in schema["required"]
        answers = schema["properties"]["answers"]
        assert answers["minItems"] == answers["maxItems"] == len(criteria)

    def test_a_refused_answer_is_logged_with_its_reason_and_never_the_text(self, monkeypatch):
        p = make_pipeline(script=lambda request: {"label": "maybe"})

        with structlog.testing.capture_logs() as logs:
            # Loggers are cached on first use (observability.py); a fresh one
            # reports to this capture whatever ran before.
            monkeypatch.setattr(screening, "_log", structlog.get_logger(screening.__name__))
            _run(p)

        refused = [entry for entry in logs if entry["event"] == "screening_answer_refused"]
        assert len(refused) == len(p.model.requests) == 8
        assert {entry["attempt"] for entry in refused} == {1, 2}
        assert all(entry["reason"] for entry in refused)
        assert all(
            set(entry) <= {"event", "log_level", "reason", "attempt", "run_id", "paper_id"}
            for entry in refused
        )
        written = " ".join(str(entry) for entry in refused)
        assert not any(record.abstract and record.abstract[:30] in written for record in CORPUS)

    def test_an_answer_that_contradicts_its_criteria_counts_as_unreadable(self):
        p = make_pipeline(script=lambda request: answer("include", "high", ["I1", "E1"]))

        run_id = _run(p)

        assert all(d.is_unreadable for d in _decisions(p, run_id))

    def test_a_model_server_that_is_down_fails_the_stage_plainly_and_keeps_decisions(self):
        p = make_pipeline()
        p.model = ObservedInference(reasonable_answer, fail_with=model_down(), fail_after=2)

        run_id = _run(p)

        assert p.rt.stored_run(run_id).state is RunState.FAILED
        error = p.failure(run_id) or ""
        assert "model server" in error
        assert "10.0.0.9" not in error
        assert len(_decisions(p, run_id)) == 2

    def test_a_paper_the_privacy_check_refuses_is_counted_failed_and_not_decided(self):
        p = make_pipeline()
        p.guard = FakePromptGuard("1 PERSON entity")

        run_id = _run(p)

        assert _decisions(p, run_id) == []
        assert p.summary(run_id, "screen")["failed"] == 4
        assert p.rt.stored_run(run_id).state is RunState.DONE

    def test_a_review_without_an_accepted_plan_fails_plainly(self):
        p = make_pipeline()
        run_id = p.rt.start_run(("discover", "ingest"))
        run_until_idle(p.rt.worker(p.stages()))
        p.plans.plans.clear()

        second = _run(p, ("screen",))

        assert "accepted plan" in (p.failure(second) or "")
        assert run_id != second

    def test_a_plan_without_criteria_fails_plainly(self):
        p = make_pipeline()
        p.plans.accept(p.rt.review_id, accepted_plan(p.rt.review_id, criteria=()))

        run_id = _run(p)

        assert "criteria" in (p.failure(run_id) or "")

    def test_every_decision_is_audited_with_ids_only(self):
        p = make_pipeline()

        run_id = _run(p)

        decided = [
            e
            for e in project_events(p.rt.harness, p.rt.project_id)
            if e.entry.action == "screening.decided"
        ]
        assert len(decided) == 4
        assert {e.entry.target_type for e in decided} == {"paper"}
        assert {e.entry.target_id for e in decided} == {
            str(d.paper_id) for d in _decisions(p, run_id)
        }

    def test_rationales_are_stored_only_sealed(self):
        p = make_pipeline()

        _run(p)

        stored = p.rt.harness.storage.state.screening_decisions.values()
        assert stored
        assert not any(b"Decided" in d.rationale_sealed for d in stored)


class TestExport:
    def test_the_summary_carries_prisma_counts_and_the_dataset(self):
        p = make_pipeline()

        run_id = _run(p)

        summary = p.summary(run_id, "export")
        assert summary["prisma"] == {
            "identified": 6,
            "identified_by_source": {"first": 4, "second": 2},
            "failed_sources": [],
            "duplicates_removed": 1,
            "after_deduplication": 5,
            "screened": 4,
            "included": 3,
            "excluded": 1,
            "uncertain": 0,
            "not_screened": 1,
            "excluded_by_reason": {"E1": 1, "I1": 1},
        }
        dataset = summary["dataset"]
        assert dataset["papers"] == 5
        assert dataset["by_source"] == {"first": 4, "second": 1}
        assert dataset["years"] == {"earliest": 2020, "latest": 2024}
        assert any("download" in gap.lower() for gap in summary["gaps"])


class TestEvidence:
    """What the brief stage reads: included papers' masked passages, tagged by place."""

    def test_only_included_papers_are_evidence(self):
        p = make_pipeline()
        run_id = _run(p)

        async def read() -> tuple:
            async with p.rt.harness.providers.storage.unit_of_work() as uow:
                return await p.evidence.included_passages(
                    uow, UUID(p.rt.review_id), UUID(run_id)
                )

        passages = asyncio.run(read())
        included = {
            d.paper_id for d in _decisions(p, run_id) if d.label is ScreeningLabel.INCLUDE
        }
        assert {x.paper_id for x in passages} == included
        assert all(x.source_kind is PassageSourceKind.PAPER for x in passages)
        assert all(x.locator == "abstract" for x in passages)
        assert all(PLACE not in x.text for x in passages)
        assert any(MASKED_PLACE in x.text for x in passages)

    def test_tags_are_the_papers_places_in_the_run_and_in_order(self):
        p = make_pipeline()
        run_id = _run(p)
        positions = {m.paper_id: m.position for m in _run_papers(p, run_id)}

        async def read() -> tuple:
            async with p.rt.harness.providers.storage.unit_of_work() as uow:
                return await p.evidence.included_passages(
                    uow, UUID(p.rt.review_id), UUID(run_id)
                )

        passages = asyncio.run(read())
        assert [tag_number(x.tag) for x in passages] == sorted(
            positions[x.paper_id] for x in passages
        )
        assert all(x.tag == f"P{positions[x.paper_id]}" for x in passages)

    def test_another_reviews_run_yields_nothing(self):
        p = make_pipeline()
        run_id = _run(p)

        async def read() -> tuple:
            async with p.rt.harness.providers.storage.unit_of_work() as uow:
                return await p.evidence.included_passages(uow, UUID(int=1), UUID(run_id))

        assert asyncio.run(read()) == ()


class TestDeletedProjects:
    def test_deleting_the_project_makes_papers_texts_and_rationales_unreadable(self):
        p = make_pipeline()
        run_id = _run(p)
        assert send(p.rt.owner, "DELETE", f"{PROJECTS}/{p.rt.project_id}").status_code == 204

        async def read_one() -> None:
            async with p.rt.harness.providers.storage.unit_of_work() as uow:
                run = await uow.runs.get(UUID(run_id))
                assert run is not None
                (first, *_) = await p.store.run_papers(uow, run)
                await p.store.open_record(uow, run.project_id, first.paper_id)

        with pytest.raises(KeyDestroyedError):
            asyncio.run(read_one())
