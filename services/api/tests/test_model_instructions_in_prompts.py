"""Requirements for where a project's instructions reach the model, and what is kept of it.

Screening calls get the screening text; the brief's Writer and the follow-up Writer get the
writing text. The Planner, the Critics and plan chats get neither. The text is added after
the built-in prompt, masked as typed text is, and a run uses the version in force when it
was started. The run manifest, the brief's trace and the answer record which version.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from app.application.screening import screening_request
from app.domain.encryption import EncryptionContext
from app.domain.inference import ChatRequest
from app.domain.masking import MaskingPolicy
from app.domain.model_instructions import INSTRUCTIONS_HEADING
from tests.brief_support import answer_stage, role_of, stored_brief, write_brief
from tests.pipeline_support import ALL_STAGES, Pipeline, accepted_plan, make_pipeline
from tests.project_support import send
from tests.question_support import make_ask_team
from tests.run_support import RunTeam, make_run_team, run_until_idle

SCREENING = "Treat studies in refugee camps as displaced settings."
WRITING = "Write for a field manager: short sentences, no jargon."
TERM = "Kofi Mensah"
EMAIL = "amina.yusuf@example.org"
LOCAL = MaskingPolicy(is_model_hosted=False)


def save_instructions(
    rt: RunTeam, screening: str = "", writing: str = "", based_on: int | None = None
) -> dict[str, Any]:
    response = send(
        rt.owner,
        "PUT",
        f"/api/v1/projects/{rt.project_id}/model-instructions",
        {"screening": screening, "writing": writing, "based_on_version": based_on},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def mask_always(rt: RunTeam, term: str = TERM) -> None:
    response = send(
        rt.owner, "POST", f"/api/v1/projects/{rt.project_id}/masking-rules", {"term": term}
    )
    assert response.status_code == 201, response.text


def system_of(request: ChatRequest) -> str:
    return request.messages[0].content


def sent(request: ChatRequest) -> str:
    return "\n".join(m.content for m in request.messages)


def run_pipeline(p: Pipeline) -> str:
    run_id = p.rt.start_run(list(ALL_STAGES))
    run_until_idle(p.rt.worker(p.stages()))
    assert p.failure(run_id) is None
    return run_id


class TestScreening:
    def test_the_screening_text_follows_the_built_in_prompt_in_every_call(self):
        p = make_pipeline()
        save_instructions(p.rt, screening=SCREENING)

        run_pipeline(p)

        assert p.model.requests
        for request in p.model.requests:
            built_in = system_of(screening_request(accepted_plan(p.rt.review_id), "x"))
            system = system_of(request)
            assert system.startswith(built_in)
            assert system.endswith(f"{INSTRUCTIONS_HEADING}\n{SCREENING}")

    def test_the_writing_text_is_never_sent_to_screening(self):
        p = make_pipeline()
        save_instructions(p.rt, screening=SCREENING, writing=WRITING)

        run_pipeline(p)

        assert not any(WRITING in sent(r) for r in p.model.requests)

    def test_without_instructions_the_screening_prompt_is_the_built_in_one(self):
        p = make_pipeline()

        run_id = run_pipeline(p)

        built_in = system_of(screening_request(accepted_plan(p.rt.review_id), "x"))
        assert {system_of(r) for r in p.model.requests} == {built_in}
        assert "instructions_version" not in p.summary(run_id, "screen")["manifest"]

    def test_the_run_manifest_records_the_version_screening_used(self):
        p = make_pipeline()
        save_instructions(p.rt, screening="An older text.")
        save_instructions(p.rt, screening=SCREENING, based_on=1)

        run_id = run_pipeline(p)

        assert p.summary(run_id, "screen")["manifest"]["instructions_version"] == 2
        manifest = p.rt.viewer.get(f"/api/v1/runs/{run_id}/manifest").json()
        assert manifest["instructions_versions"] == [2]

    def test_instructions_saved_after_a_run_was_started_do_not_change_it(self):
        p = make_pipeline()
        save_instructions(p.rt, screening=SCREENING)
        run_id = p.rt.start_run(list(ALL_STAGES))
        p.rt.clock.advance(minutes=1)
        save_instructions(p.rt, screening="A newer text the run never saw.", based_on=1)

        run_until_idle(p.rt.worker(p.stages()))

        assert not any("newer text" in sent(r) for r in p.model.requests)
        assert all(system_of(r).endswith(SCREENING) for r in p.model.requests)
        assert p.summary(run_id, "screen")["manifest"]["instructions_version"] == 1

    def test_a_term_masked_after_the_text_was_saved_is_masked_before_it_is_sent(self):
        p = make_pipeline()
        save_instructions(p.rt, screening=f"Ask whether {TERM} ran the study.")
        mask_always(p.rt)

        run_pipeline(p)

        assert p.model.requests
        assert not any("Kofi" in sent(r) for r in p.model.requests)
        assert all("[PERSON_1]" in system_of(r) for r in p.model.requests)


class TestMaskingChoice:
    """The instructions follow the project's masking choice, as other typed text does."""

    def test_with_masking_off_the_screening_text_is_sent_as_written(self):
        p = make_pipeline(masking_policy=LOCAL)
        save_instructions(p.rt, screening=f"Ask {EMAIL} about camp studies.")

        run_pipeline(p)

        assert p.model.requests
        assert all(EMAIL in system_of(r) for r in p.model.requests)

    def test_with_masking_on_an_identifier_in_the_screening_text_is_masked(self):
        p = make_pipeline()
        save_instructions(p.rt, screening=f"Ask {EMAIL} about camp studies.")

        run_pipeline(p)

        assert p.model.requests
        assert not any(EMAIL in sent(r) for r in p.model.requests)

    def test_with_masking_off_a_rule_term_is_still_masked(self):
        p = make_pipeline(masking_policy=LOCAL)
        save_instructions(p.rt, screening=f"Ask whether {TERM} ran the study.")
        mask_always(p.rt)

        run_pipeline(p)

        assert p.model.requests
        assert not any("Kofi" in sent(r) for r in p.model.requests)


def _brief(rt: RunTeam) -> tuple[Any, dict[str, Any]]:
    rig = answer_stage(rt)
    run_id = write_brief(rt, rig)
    record = stored_brief(rt, run_id)
    assert record is not None
    trace = rt.viewer.get(f"/api/v1/briefs/{record.id}/trace").json()
    return rig, trace


class TestBrief:
    def test_only_the_writer_gets_the_writing_text_after_its_built_in_prompt(self):
        rt = make_run_team()
        save_instructions(rt, screening=SCREENING, writing=WRITING)

        rig, _ = _brief(rt)

        roles = {role_of(r): system_of(r) for r in rig.provider.requests}
        assert roles["writer"].endswith(f"{INSTRUCTIONS_HEADING}\n{WRITING}")
        assert roles["writer"].index(INSTRUCTIONS_HEADING) > roles["writer"].index("JSON only")
        for role in ("planner", "critic"):
            assert INSTRUCTIONS_HEADING not in roles[role]
        assert not any(SCREENING in sent(r) for r in rig.provider.requests)

    def test_the_trace_shows_the_text_as_sent_and_the_manifest_its_version(self):
        rt = make_run_team()
        save_instructions(rt, writing=WRITING)

        _, trace = _brief(rt)

        assert trace["instructions"] == {"version": 1, "text": WRITING}
        assert trace["manifest"]["instructions_version"] == 1

    def test_the_trace_shows_the_masked_text_when_masking_applies(self):
        rt = make_run_team()
        save_instructions(rt, writing=f"Write for {TERM}'s team.")
        mask_always(rt)

        rig, trace = _brief(rt)

        assert trace["instructions"]["text"] == "Write for [PERSON_1]'s team."
        assert not any("Kofi" in sent(r) for r in rig.provider.requests)

    def test_without_instructions_the_trace_has_none(self):
        rt = make_run_team()

        _, trace = _brief(rt)

        assert trace["instructions"] is None
        assert "instructions_version" not in trace["manifest"]


class TestFollowUpAnswers:
    def test_only_the_writer_gets_the_writing_text_and_the_answer_records_its_version(self):
        team = make_ask_team()
        save_instructions(team.rt, screening=SCREENING, writing=WRITING)

        question_id = team.ask().json()["id"]
        team.answer_all()

        writers = [r for r in team.provider.requests if system_of(r).startswith("You answer")]
        critics = [r for r in team.provider.requests if r not in writers]
        assert writers
        assert critics
        assert all(system_of(r).endswith(f"{INSTRUCTIONS_HEADING}\n{WRITING}") for r in writers)
        assert not any(INSTRUCTIONS_HEADING in system_of(r) for r in critics)
        assert not any(SCREENING in sent(r) for r in team.provider.requests)
        stored = _stored_answer(team.rt, team.stored(question_id))
        assert stored["manifest"]["instructions_version"] == 1
        assert stored["instructions"] == {"version": 1, "text": WRITING}


def _stored_answer(rt: RunTeam, record: Any) -> dict[str, Any]:
    context = EncryptionContext(UUID(rt.project_id), "questions.answer", str(record.id))

    async def read() -> bytes:
        async with rt.harness.providers.storage.unit_of_work() as uow:
            return await rt.harness.providers.encryption.decrypt(
                uow, context, record.answer_sealed
            )

    answer: dict[str, Any] = json.loads(asyncio.run(read()))
    return answer
