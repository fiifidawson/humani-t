"""Typed run-event payloads: what the web app reads from the stream without parsing strings.

Sources: UI_UX_SPEC §6.6 (the vocabulary gate is the draft canvas), §6.7 (one
live row per screened paper, with its label and one-line reason), §11.2 (each
event's fields). The older string fields stay, deprecated, while the web app
moves over; both say the same thing, and both carry masked text only.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.schemas.run_events import (
    RunStreamEvent,
    ScreenedItemData,
    VocabularyGatePayload,
)
from app.domain.runs import GateKind
from app.inference.fake import FakePromptGuard
from app.runs.fake import FakeStage
from tests.pipeline_support import ALL_STAGES, PLACE, Pipeline, make_pipeline
from tests.project_support import send
from tests.run_support import RunTeam, make_run_team, parse_sse, run_until_idle

_VOCABULARY = "[intervention]\ncash transfer; voucher\n[outcome]\nmalnutrition"


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def _screened(p: Pipeline, run_id: str) -> list[dict[str, Any]]:
    return [row["screened"] for row in p.progress(run_id, "screen")]


class TestProgressTimestamps:
    def test_every_progress_event_says_when_it_happened(self, rt):
        run_id = rt.start_run(["queries"])
        rt.clock.advance(minutes=3)
        run_until_idle(rt.worker({"queries": FakeStage(items=2)}))

        progress = [e.data for e in rt.events(run_id) if e.name == "stage.progress"]

        assert progress
        assert {p["at"] for p in progress} == {rt.clock().isoformat()}


class TestScreenedItems:
    def test_each_screened_paper_has_a_structured_row_beside_the_old_string(self):
        p = make_pipeline()
        run_id = p.rt.start_run(ALL_STAGES)
        run_until_idle(p.rt.worker(p.stages()))

        rows = p.progress(run_id, "screen")

        assert len(rows) == 4
        for row in rows:
            item = ScreenedItemData.model_validate(row["screened"])
            assert item.outcome == "screened"
            assert item.reason in row["item"]
            assert item.title is not None
            assert item.title in row["item"]
        tiers = {r["screened"]["tier"] for r in rows}
        assert tiers == {"highly_relevant", "relevant", "excluded"}
        assert {r["screened"]["label"] for r in rows} == {"include", "exclude"}
        assert {r["screened"]["priority"] for r in rows} <= {"high", "medium", "low"}

    def test_the_rows_name_the_papers_the_paper_list_shows(self):
        p = make_pipeline()
        run_id = p.rt.start_run(ALL_STAGES)
        run_until_idle(p.rt.worker(p.stages()))

        listed = p.rt.viewer.get(f"/api/v1/reviews/{p.rt.review_id}/papers").json()

        decided = {row["paper_id"] for row in listed["papers"] if row["decision"]}
        assert {s["paper_id"] for s in _screened(p, run_id)} == decided

    def test_the_rows_carry_masked_text_only(self):
        p = make_pipeline()
        run_id = p.rt.start_run(ALL_STAGES)
        run_until_idle(p.rt.worker(p.stages()))

        for item in _screened(p, run_id):
            assert PLACE not in str(item)
            assert "\n" not in (item["title"] or "") + (item["reason"] or "")
            assert "Second line" not in (item["reason"] or "")

    def test_a_paper_the_privacy_check_stopped_is_a_failed_row_with_no_text_of_its_own(self):
        p = make_pipeline()
        p.guard = FakePromptGuard("1 PERSON entity")
        run_id = p.rt.start_run(ALL_STAGES)
        run_until_idle(p.rt.worker(p.stages()))

        items = _screened(p, run_id)

        assert items
        for item in items:
            assert ScreenedItemData.model_validate(item).outcome == "failed"
            assert item["title"] is None
            assert (item["label"], item["priority"], item["tier"]) == (None, None, None)


class TestVocabularyGatePayload:
    def _gated_run(self, rt: RunTeam) -> tuple[str, dict[str, Any]]:
        plan = f"/api/v1/reviews/{rt.review_id}/plan/vocabulary"
        body = {"format": "text", "content": _VOCABULARY}
        assert send(rt.owner, "PUT", plan, body).status_code == 200
        run_id = rt.start_run(["queries"])
        run_until_idle(rt.worker({"queries": rt.harness.providers.stages["queries"]}))
        required = [e.data for e in rt.events(run_id) if e.name == "gate.required"]
        assert required
        assert required[0]["kind"] == GateKind.VOCABULARY.value
        payload: dict[str, Any] = required[0]["payload"]
        return run_id, payload

    def test_the_vocabulary_gate_payload_matches_its_published_type_exactly(self, rt):
        _, payload = self._gated_run(rt)

        typed = VocabularyGatePayload.model_validate(payload)

        assert typed.model_dump(mode="json") == payload
        assert typed.version == 1
        assert typed.is_strict is True
        assert [t.name for t in typed.themes] == ["intervention", "outcome"]

    def test_the_stream_parses_the_gate_payload_as_the_vocabulary_type(self, rt):
        run_id, _ = self._gated_run(rt)
        assert rt.cancel(run_id).status_code == 202

        _, events, _ = parse_sse(rt.stream(run_id).text)

        gate = next(e for e in events if e.name == "gate.required")
        parsed = RunStreamEvent.model_validate(
            {"id": gate.id, "event": gate.name, "data": gate.data}
        ).root
        assert isinstance(parsed.data.payload, VocabularyGatePayload)  # type: ignore[union-attr]


class TestPublishedSchemas:
    def test_the_new_types_are_published_in_openapi(self, rt):
        components = rt.owner.get("/openapi.json").json()["components"]["schemas"]

        assert {"VocabularyGatePayload", "ScreenedItemData"} <= set(components)
        progress = components["StageProgressData"]["properties"]
        assert {"at", "screened", "item"} <= set(progress)
        payload = components["GateRequiredData"]["properties"]["payload"]
        assert {"$ref": "#/components/schemas/VocabularyGatePayload"} in payload["anyOf"]
