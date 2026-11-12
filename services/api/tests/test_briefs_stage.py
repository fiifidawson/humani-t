"""The `answer` stage: write the decision brief, store it once, and report every step.

Everything is real except the model (scripted), the stored evidence (a fake source) and the
question masker (a fake that masks one place name). The stage may run more than once, and
only masked text reaches the model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from uuid import UUID

import pytest

from app.domain.briefs import BriefRecord, StopReason
from app.domain.encryption import DecryptionError, EncryptionContext, KeyDestroyedError
from app.domain.inference import ChatRequest
from app.domain.runs import JsonValue, RunState
from app.inference.fake import FakePromptGuard
from app.runs.base import Stage, StageContext
from tests.brief_support import (
    MASKED_PLACE,
    SENSITIVE_PLACE,
    RoleScript,
    answer_stage,
    critic_answer,
    planner_answer,
    role_of,
    stored_brief,
    text_sent,
    write_brief,
    writer_answer,
)
from tests.project_support import PROJECTS, project_events, send
from tests.run_support import (
    LEASE,
    QUESTION,
    RunTeam,
    SimulatedCrashError,
    current_of,
    make_run_team,
    names,
    run_until_idle,
)


@pytest.fixture
def rt() -> RunTeam:
    assert SENSITIVE_PLACE in QUESTION
    return make_run_team()


def brief_of(rt: RunTeam, run_id: str) -> BriefRecord:
    record = stored_brief(rt, run_id)
    assert record is not None, "the run stored no brief"
    return record


class TestWritingTheBrief:
    def test_a_run_of_the_answer_stage_stores_one_brief_for_the_run(self, rt):
        run_id = write_brief(rt)

        record = brief_of(rt, run_id)
        assert rt.stored_run(run_id).outcome is RunState.DONE
        assert (record.version, record.review_id, record.run_id) == (
            1,
            UUID(rt.review_id),
            UUID(run_id),
        )
        assert (record.stopped_because, record.rounds, record.kept_round) == (
            StopReason.ACCEPTED,
            1,
            1,
        )
        assert record.model_id == "qwen3.5:9b"

    def test_each_run_of_the_stage_writes_the_next_version(self, rt):
        write_brief(rt)
        second = write_brief(rt)

        assert brief_of(rt, second).version == 2

    def test_every_agent_step_is_sent_as_an_agent_step_event(self, rt):
        run_id = write_brief(rt)

        steps = [e.data for e in rt.events(run_id) if e.name == "agent.step"]
        assert [(s["round"], s["role"], s["status"]) for s in steps] == [
            (0, "planner", "ok"),
            (0, "retriever", "ok"),
            (1, "writer", "ok"),
            (1, "checks", "ok"),
            (1, "critic", "ok"),
        ]

    def test_the_stage_summary_names_the_brief_and_carries_the_manifest(self, rt):
        run_id = write_brief(rt)

        finished = next(e for e in rt.events(run_id) if e.name == "stage.finished")
        summary = finished.data["summary"]
        record = brief_of(rt, run_id)
        assert summary["brief_id"] == str(record.id)
        assert (summary["version"], summary["stopped_because"], summary["warnings"]) == (
            1,
            "accepted",
            0,
        )
        assert summary["manifest"]["model_ids"]["writer"] == "qwen3.5:9b"
        assert set(summary["manifest"]["prompt_versions"]) == {"planner", "writer", "critic"}

    def test_a_brief_whose_draft_cited_an_unretrieved_passage_records_the_revision(self, rt):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005"), writer_answer()],
            critic=[critic_answer(), critic_answer()],
        )
        run_id = write_brief(rt, answer_stage(rt, script))

        checks = [
            e.data
            for e in rt.events(run_id)
            if e.name == "agent.step" and e.data["role"] == "checks"
        ]
        assert [len(c["problems"]) for c in checks] == [1, 0]
        assert brief_of(rt, run_id).kept_round == 2


class TestMaskedTextOnly:
    def test_only_the_masked_question_reaches_the_model(self, rt):
        rig = answer_stage(rt)

        write_brief(rt, rig)

        sent = [text_sent(r) for r in rig.provider.requests]
        assert sent
        assert all(SENSITIVE_PLACE not in text for text in sent)
        assert MASKED_PLACE in sent[0]

    def test_no_event_carries_the_unmasked_question(self, rt):
        run_id = write_brief(rt)

        assert SENSITIVE_PLACE not in json.dumps([e.data for e in rt.events(run_id)])

    def test_the_brief_keeps_the_question_as_it_was_sent(self, rt):
        run_id = write_brief(rt)

        assert MASKED_PLACE in asyncio.run(_opened_question(rt, run_id))


async def _opened_question(rt: RunTeam, run_id: str) -> str:
    current = await current_of(rt, rt.owner)
    opened = await rt.harness.providers.briefs.for_review(
        current, UUID(rt.review_id), UUID(run_id)
    )
    return opened.brief.question


class CrashAfterwards:
    """Lets the real stage finish, then kills the worker before the stage is marked done."""

    def __init__(self, inner: Stage, times: int = 1) -> None:
        self.inner = inner
        self.remaining = times

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        summary = await self.inner.run(context)
        if self.remaining:
            self.remaining -= 1
            raise SimulatedCrashError
        return summary


class TestRunningAgain:
    def test_a_rerun_after_a_crash_keeps_one_brief_and_asks_the_model_nothing_more(self, rt):
        rig = answer_stage(rt)
        run_id = rt.start_run(["answer"])
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker({"answer": CrashAfterwards(rig.stage)}))
        calls = len(rig.provider.requests)

        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker({"answer": rig.stage}, "worker-b"))

        assert rt.stored_run(run_id).outcome is RunState.DONE
        assert len(rig.provider.requests) == calls
        assert asyncio.run(_count_briefs(rt)) == 1
        finished = [e for e in rt.events(run_id) if e.name == "stage.finished"]
        assert finished[0].data["summary"]["brief_id"] == str(brief_of(rt, run_id).id)

    def test_a_crash_before_the_brief_is_stored_restarts_the_loop_cleanly(self, rt):
        crashes = {"left": 1}

        def crash_at_the_first_draft(request: ChatRequest) -> None:
            if role_of(request) == "writer" and crashes["left"]:
                crashes["left"] -= 1
                raise SimulatedCrashError

        script = RoleScript(
            planner=[planner_answer(), planner_answer()],
            writer=[writer_answer()],
            critic=[critic_answer()],
        )
        rig = answer_stage(rt, script, before_answer=crash_at_the_first_draft)
        run_id = rt.start_run(["answer"])
        with pytest.raises(SimulatedCrashError):
            run_until_idle(rt.worker({"answer": rig.stage}))

        rt.clock.advance(seconds=LEASE.total_seconds() + 1)
        run_until_idle(rt.worker({"answer": rig.stage}, "worker-b"))

        assert rt.stored_run(run_id).outcome is RunState.DONE
        assert asyncio.run(_count_briefs(rt)) == 1


async def _count_briefs(rt: RunTeam) -> int:
    async with rt.harness.providers.storage.unit_of_work() as uow:
        return await uow.briefs.count_for_review(UUID(rt.review_id))


class TestFailures:
    def test_a_run_with_no_included_papers_fails_in_plain_words(self, rt):
        rig = answer_stage(rt, passages=())
        run_id = write_brief(rt, rig)

        finished = rt.events(run_id)[-1]
        assert finished.data["outcome"] == "failed"
        assert "included" in finished.data["error"]
        assert rig.provider.requests == []
        assert stored_brief(rt, run_id) is None

    def test_a_refused_prompt_fails_the_run_and_stores_no_brief(self, rt):
        rig = answer_stage(rt, guard=FakePromptGuard("1 unmasked name"))
        run_id = write_brief(rt, rig)

        finished = rt.events(run_id)[-1]
        assert finished.data["outcome"] == "failed"
        assert "privacy check" in finished.data["error"]
        assert stored_brief(rt, run_id) is None

    def test_cancelling_during_the_loop_ends_the_run_without_a_brief(self, rt):
        run_ids: list[str] = []

        def cancel_while_planning(request: ChatRequest) -> None:
            assert rt.cancel(run_ids[0]).status_code == 202

        rig = answer_stage(rt, before_answer=cancel_while_planning)
        run_ids.append(rt.start_run(["answer"]))
        run_until_idle(rt.worker({"answer": rig.stage}))

        assert rt.stored_run(run_ids[0]).outcome is RunState.CANCELLED
        assert names(rt.events(run_ids[0])).count("agent.step") == 1
        assert stored_brief(rt, run_ids[0]) is None


class TestStoredSafely:
    def test_the_stored_brief_holds_no_text_in_the_clear(self, rt):
        record = brief_of(rt, write_brief(rt))

        stored = record.content_sealed + record.evidence_sealed + record.trace_sealed
        for text in ("Cash transfers", MASKED_PLACE, "diet diversity", "food prices"):
            assert text.encode() not in stored

    def test_the_brief_is_bound_to_its_own_row(self, rt):
        first = brief_of(rt, write_brief(rt))
        second = brief_of(rt, write_brief(rt))
        context = EncryptionContext(
            UUID(rt.project_id), "decision_briefs.content", str(second.id)
        )

        async def open_first_as_second() -> bytes:
            async with rt.harness.providers.storage.unit_of_work() as uow:
                return await rt.harness.providers.encryption.decrypt(
                    uow, context, first.content_sealed
                )

        with pytest.raises(DecryptionError):
            asyncio.run(open_first_as_second())

    def test_deleting_the_project_makes_the_brief_unreadable(self, rt):
        record = brief_of(rt, write_brief(rt))

        assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204

        context = EncryptionContext(
            UUID(rt.project_id), "decision_briefs.content", str(record.id)
        )

        async def read() -> bytes:
            async with rt.harness.providers.storage.unit_of_work() as uow:
                return await rt.harness.providers.encryption.decrypt(
                    uow, context, record.content_sealed
                )

        with pytest.raises(KeyDestroyedError):
            asyncio.run(read())

    def test_writing_a_brief_is_audited_with_ids_only(self, rt):
        run_id = write_brief(rt)
        record = brief_of(rt, run_id)

        written = [
            e
            for e in project_events(rt.harness, rt.project_id)
            if e.entry.action == "brief.created"
        ]
        assert len(written) == 1
        entry = written[0].entry
        assert (entry.target_type, entry.target_id, entry.actor_id) == (
            "decision_brief",
            str(record.id),
            None,
        )
