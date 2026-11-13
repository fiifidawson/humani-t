"""Masking as a project's choice (ADR 0014): the owner's switch, and the operator's say over it.

The switch is read each time text is about to leave, never trusted from storage alone: a
project switched off under a local model is masked again once the model is hosted, unless
the operator allows otherwise. With masking off, the project's own rules still apply.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.application.documents import DocumentEvidence, DocumentReviewQueue
from app.application.inference import GuardedInference
from app.application.masking import MaskStage, PaperMaskStore
from app.domain.discovery import IngestedText, TextKind
from app.domain.inference import ChatMessage, ChatRequest, ChatRole, PromptRefusedError
from app.domain.masking import (
    PAPER_ENTITY_TYPES,
    EntityType,
    MaskingPolicy,
    MaskingRule,
    ProjectPrivacy,
)
from app.inference.fake import FakeInferenceProvider
from app.malware.fake import EICAR
from app.privacy.guard import LeakCheckPromptGuard
from app.privacy.patterns import PatternDetector
from app.privacy.sanitizer import DetectorSanitizer
from app.storage.base import UnitOfWork
from tests.brief_support import answer_stage, write_brief
from tests.document_support import make_document_team
from tests.plan_support import make_plan_team
from tests.project_support import (
    PASSWORD,
    PROJECTS,
    Team,
    make_team,
    project_events,
    send,
)
from tests.question_support import QUESTION, make_ask_team
from tests.run_support import RunTeam, add_rule, make_run_team, run_until_idle
from tests.support import AuthHarness, csrf_headers

LOCAL = MaskingPolicy(is_model_hosted=False)
HOSTED = MaskingPolicy(is_model_hosted=True)
HOSTED_ALLOWED = MaskingPolicy(is_model_hosted=True, allows_unmasked_hosted=True)

EMAIL = "amina.yusuf@example.org"
STAFF = "Kofi Mensah"
ABSTRACT = f"Data were collected by {STAFF} in Aden. Correspondence: {EMAIL}."
REPORT = f"Interview with {STAFF} ({EMAIL}) about the clinic.".encode()

REFUSAL = "This deployment sends text to a hosted model, so masking stays on."


def switch(browser: TestClient, project_id: str, *, on: bool) -> Response:
    return send(browser, "PUT", f"{PROJECTS}/{project_id}/masking", {"is_masking_on": on})


def state(body: dict[str, Any]) -> tuple[bool, bool, bool]:
    return (body["is_masking_on"], body["is_masking_effective"], body["can_turn_masking_off"])


def read_state(browser: TestClient, project_id: str) -> tuple[bool, bool, bool]:
    response = browser.get(f"{PROJECTS}/{project_id}")
    assert response.status_code == 200, response.text
    return state(response.json())


def store_switch(harness: AuthHarness, project_id: str, *, on: bool) -> None:
    """Write the switch straight to storage: a project switched off under another model."""

    async def write() -> None:
        async with harness.providers.storage.unit_of_work() as uow:
            project = await uow.projects.get(UUID(project_id))
            assert project is not None
            await uow.projects.save(dataclasses.replace(project, is_masking_on=on))
            await uow.commit()

    asyncio.run(write())


def reauthenticate(browser: TestClient) -> None:
    response = send(browser, "POST", "/api/v1/auth/reauthenticate", {"password": PASSWORD})
    assert response.status_code == 204


class TestTheSwitch:
    def test_a_new_project_starts_masked_when_the_model_is_hosted(self):
        team = make_team(HOSTED)

        assert read_state(team.owner, team.project_id) == (True, True, False)

    def test_a_new_project_starts_unmasked_when_the_model_runs_in_the_deployment(self):
        team = make_team(LOCAL)

        assert read_state(team.owner, team.project_id) == (False, False, True)

    def test_an_owner_turns_masking_on_and_off_again(self):
        team = make_team(LOCAL)

        turned_on = switch(team.owner, team.project_id, on=True)
        turned_off = switch(team.owner, team.project_id, on=False)

        assert turned_on.status_code == 200, turned_on.text
        assert state(turned_on.json()) == (True, True, True)
        assert state(turned_off.json()) == (False, False, True)

    def test_turning_masking_off_is_refused_while_the_model_is_hosted(self):
        team = make_team(HOSTED)

        response = switch(team.owner, team.project_id, on=False)

        assert response.status_code == 409
        assert response.json()["detail"] == REFUSAL
        assert read_state(team.owner, team.project_id) == (True, True, False)

    def test_with_the_operators_permission_an_owner_may_turn_it_off_for_a_hosted_model(self):
        team = make_team(HOSTED_ALLOWED)
        assert read_state(team.owner, team.project_id) == (True, True, True)

        response = switch(team.owner, team.project_id, on=False)

        assert state(response.json()) == (False, False, True)

    def test_a_project_switched_off_is_masked_again_once_the_model_is_hosted(self):
        team = make_team(HOSTED)
        store_switch(team.harness, team.project_id, on=False)

        assert read_state(team.owner, team.project_id) == (False, True, False)

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_only_owners_change_the_switch(self, role):
        team = make_team(LOCAL)

        response = switch(team.browser_for(role), team.project_id, on=True)

        assert response.status_code == 403
        assert read_state(team.owner, team.project_id)[0] is False

    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_sees_the_masking_state(self, role):
        team = make_team(HOSTED)
        browser = team.browser_for(role)

        listed = browser.get(PROJECTS).json()

        assert read_state(browser, team.project_id) == (True, True, False)
        assert [state(p) for p in listed] == [(True, True, False)]

    def test_turning_masking_off_needs_a_recent_sign_in(self):
        team = make_team(LOCAL)
        switch(team.owner, team.project_id, on=True)
        team.harness.clock.advance(minutes=16)

        refused = switch(team.owner, team.project_id, on=False)
        reauthenticate(team.owner)
        accepted = switch(team.owner, team.project_id, on=False)

        assert refused.status_code == 401
        assert refused.json()["reauthentication_required"] is True
        assert accepted.status_code == 200

    def test_turning_masking_on_needs_no_recent_sign_in(self):
        team = make_team(LOCAL)
        team.harness.clock.advance(minutes=16)

        assert switch(team.owner, team.project_id, on=True).status_code == 200

    def test_each_change_is_audited_with_its_new_value(self):
        team = make_team(LOCAL)
        switch(team.owner, team.project_id, on=True)
        switch(team.owner, team.project_id, on=False)

        changes = [
            e
            for e in project_events(team.harness, team.project_id)
            if e.entry.action == "project.masking_changed"
        ]

        assert [_details(team, e) for e in changes] == [
            {"is_masking_on": True},
            {"is_masking_on": False},
        ]

    def test_the_body_must_say_on_or_off(self):
        team = make_team(LOCAL)

        response = send(team.owner, "PUT", f"{PROJECTS}/{team.project_id}/masking", {})

        assert response.status_code == 422


def _details(team: Team, event: Any) -> Any:
    providers = team.harness.providers

    async def read() -> Any:
        async with providers.storage.unit_of_work() as uow:
            return await providers.audit.read_details(uow, event)

    return asyncio.run(read())


class TestPlanning:
    def test_with_masking_off_the_question_is_sent_without_the_detectors(self):
        team = make_plan_team(masking_policy=LOCAL)

        team.drafted()

        assert "in Marib?" in team.model.sent_text()
        assert team.sanitizer.inputs == []

    def test_with_masking_off_the_projects_rules_still_mask_their_terms(self):
        team = make_plan_team(masking_policy=LOCAL)
        add_rule(team.rt, "Marib", "LOCATION")

        team.drafted()

        sent = team.model.sent_text()
        assert "Marib" not in sent
        assert "[LOCATION_1]" in sent

    def test_each_receipt_says_whether_masking_was_on(self):
        unmasked = make_plan_team(masking_policy=LOCAL)
        masked = make_plan_team()
        unmasked.drafted()
        masked.drafted()

        assert [r["is_masked"] for r in unmasked.plan()["receipts"]] == [False]
        assert [r["is_masked"] for r in masked.plan()["receipts"]] == [True]

    def test_a_project_switched_off_is_masked_once_the_model_is_hosted(self):
        team = make_plan_team()
        store_switch(team.rt.harness, team.rt.project_id, on=False)

        team.drafted()

        assert "Marib" not in team.model.sent_text()
        assert team.plan()["receipts"][0]["is_masked"] is True


def _request(text: str) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage(ChatRole.USER, text),),
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )


def _guarded() -> GuardedInference:
    provider = FakeInferenceProvider(lambda _: {"ok": True})
    return GuardedInference(provider, LeakCheckPromptGuard((PatternDetector(),)))


class TestThePatternGuard:
    RULE = MaskingRule(uuid.uuid4(), STAFF, EntityType.PERSON)

    async def test_with_masking_off_identifiers_the_owner_chose_to_send_pass(self):
        inference = _guarded().for_project(ProjectPrivacy((self.RULE,), is_masked=False))

        result = await inference.chat(_request(f"Write to {EMAIL}."))

        assert result.output == {"ok": True}

    async def test_with_masking_off_a_rule_term_is_still_refused(self):
        inference = _guarded().for_project(ProjectPrivacy((self.RULE,), is_masked=False))

        with pytest.raises(PromptRefusedError):
            await inference.chat(_request(f"Ask {STAFF}."))

    async def test_with_masking_on_an_identifier_is_refused_as_before(self):
        inference = _guarded().for_project(ProjectPrivacy((), is_masked=True))

        with pytest.raises(PromptRefusedError):
            await inference.chat(_request(f"Write to {EMAIL}."))


class _Texts:
    """An `IngestedTextSource` serving fixed raw texts, as ingest would have kept them."""

    def __init__(self, texts: Sequence[str]) -> None:
        self.by_paper = {uuid.uuid4(): text for text in texts}

    async def texts_for_run(
        self, uow: UnitOfWork, project_id: UUID, run_id: UUID
    ) -> tuple[IngestedText, ...]:
        return tuple(IngestedText(p, TextKind.ABSTRACT, t) for p, t in self.by_paper.items())


class _MaskLane:
    def __init__(self, rt: RunTeam, texts: Sequence[str] = (ABSTRACT,)) -> None:
        providers = rt.harness.providers
        self.rt = rt
        self.texts = _Texts(texts)
        self.masks = PaperMaskStore(providers.encryption, providers.masking, rt.clock)
        masker = DetectorSanitizer([PatternDetector()], entity_types=PAPER_ENTITY_TYPES)
        self.stage = MaskStage(
            storage=providers.storage,
            texts=self.texts,
            masks=self.masks,
            rules=providers.masking_rules,
            sanitizer=masker,
            detector_sources=masker.sources,
            entity_types=tuple(PAPER_ENTITY_TYPES),
            documents=DocumentReviewQueue(providers.storage),
        )

    def run(self) -> str:
        run_id = self.rt.start_run(["mask"])
        run_until_idle(self.rt.worker({"mask": self.stage}))
        return run_id

    def masked(self) -> str | None:
        (paper_id,) = self.texts.by_paper
        project_id = UUID(self.rt.project_id)

        async def read() -> str | None:
            async with self.rt.harness.providers.storage.unit_of_work() as uow:
                return await self.masks.masked_text(uow, project_id, paper_id)

        return asyncio.run(read())

    def manifest(self, run_id: str) -> dict[str, Any]:
        response = self.rt.viewer.get(f"/api/v1/runs/{run_id}/manifest")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body


class TestPapers:
    def test_with_masking_off_the_mask_stage_leaves_paper_text_as_is_but_for_rules(self):
        rt = make_run_team(LOCAL)
        add_rule(rt, STAFF)
        lane = _MaskLane(rt)

        lane.run()

        assert lane.masked() == (
            f"Data were collected by [PERSON_1] in Aden. Correspondence: {EMAIL}."
        )

    def test_the_run_manifest_records_whether_masking_was_on(self):
        unmasked = _MaskLane(make_run_team(LOCAL))
        masked = _MaskLane(make_run_team())

        assert unmasked.manifest(unmasked.run())["masking"] == "off"
        assert masked.manifest(masked.run())["masking"] == "on"

    def test_paper_text_kept_unmasked_is_not_served_once_masking_is_on_until_masked(self):
        rt = make_run_team(LOCAL)
        lane = _MaskLane(rt)
        lane.run()

        switch(rt.owner, rt.project_id, on=True)
        withheld = lane.masked()
        lane.run()

        assert withheld is None
        assert lane.masked() == (
            "Data were collected by Kofi Mensah in Aden. Correspondence: [EMAIL_1]."
        )


def _upload(rt: RunTeam, content: bytes = REPORT) -> str:
    response = rt.owner.post(
        f"/api/v1/projects/{rt.project_id}/documents",
        files={"file": ("report.txt", content, "text/plain")},
        headers=csrf_headers(rt.owner),
    )
    assert response.status_code == 202, response.text
    asyncio.run(rt.harness.providers.document_processor.run_once())
    document_id: str = response.json()["id"]
    return document_id


def _passages(harness: AuthHarness, project_id: str) -> list[str]:
    providers = harness.providers
    evidence = DocumentEvidence(providers.encryption, providers.masking_rules)

    async def read() -> list[str]:
        async with providers.storage.unit_of_work() as uow:
            return [p.text for p in await evidence.approved_passages(uow, UUID(project_id))]

    return asyncio.run(read())


class TestDocuments:
    def test_with_masking_off_a_ready_document_needs_no_privacy_review(self):
        rt = make_run_team(LOCAL)
        _upload(rt)

        run_id = _MaskLane(rt, texts=()).run()

        assert [e for e in rt.events(run_id) if e.name == "gate.required"] == []
        outcome = rt.stored_run(run_id).outcome
        assert outcome is not None
        assert outcome.value == "done"

    def test_with_masking_off_the_model_reads_the_document_as_written_but_for_rules(self):
        rt = make_run_team(LOCAL)
        add_rule(rt, STAFF)
        _upload(rt)

        (text,) = _passages(rt.harness, rt.project_id)

        assert text == f"Interview with [PERSON_1] ({EMAIL}) about the clinic."

    def test_with_masking_on_an_unreviewed_document_is_never_used(self):
        rt = make_run_team()
        _upload(rt)

        assert _passages(rt.harness, rt.project_id) == []

    def test_with_masking_off_a_document_is_still_scanned_and_an_infected_one_never_used(self):
        team = make_document_team(masking_policy=LOCAL)
        team.uploaded(EICAR)
        team.process()

        assert _passages(team.harness, team.project_id) == []


class TestFollowUpQuestions:
    def test_with_masking_off_a_question_is_sent_without_the_detectors(self):
        team = make_ask_team(masking_policy=LOCAL)

        asked = team.ask().json()
        team.answer_all()

        assert asked["question_sent"] == QUESTION
        assert asked["is_masked"] is False
        assert QUESTION in "\n".join(
            m.content for r in team.provider.requests for m in r.messages
        )

    def test_a_question_asked_unmasked_is_not_sent_once_masking_is_on(self):
        team = make_ask_team(masking_policy=LOCAL)
        question_id = team.ask().json()["id"]

        switch(team.rt.owner, team.rt.project_id, on=True)
        team.answer_all()

        failed = team.question(question_id)
        assert failed["status"] == "failed"
        assert failed["error"] == (
            "This question was asked while masking was off, and masking is on now. "
            "Ask it again."
        )
        assert team.provider.requests == []


class TestTheBrief:
    def test_the_trace_says_whether_masking_was_on(self):
        unmasked = make_run_team(LOCAL)
        masked = make_run_team()

        for rt, expected in ((unmasked, False), (masked, True)):
            write_brief(rt, answer_stage(rt))
            brief = rt.viewer.get(f"/api/v1/reviews/{rt.review_id}/brief").json()
            trace = rt.viewer.get(f"/api/v1/briefs/{brief['id']}/trace").json()
            assert trace["is_masked"] is expected
            assert "is_masked" not in trace["manifest"]
