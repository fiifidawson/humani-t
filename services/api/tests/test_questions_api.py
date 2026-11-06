"""Follow-up questions with threads (UI_UX_SPEC §6.12, §5.3.9, §5.3.2, §15 P2).

Sources: "Answers only from this project"; "Nothing in this project answers
that"; every user text sent to a model has a privacy receipt; architecture
§4.2 (claims cite retrieved passages, checked by code), §4.4 and §5.7 (slow
work in the background under a lease), §5.4 (sealed, shredded with the
project), §5.5 (404 for non-members, audit ids only).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from uuid import UUID

import pytest

from app.domain.encryption import EncryptionContext, KeyDestroyedError
from app.domain.inference import ChatRequest, ChatResult, InferenceUnavailableError
from app.domain.questions import INTERRUPTED, NOTHING_FOUND
from tests.brief_support import MASKED_PLACE, SENSITIVE_PLACE, claim, critic_answer
from tests.project_support import PROJECTS, project_events, send
from tests.question_support import (
    QUESTION,
    AnswerScript,
    answer,
    good_answer,
    make_ask_team,
)


def sent_text(request: ChatRequest) -> str:
    return "\n".join(m.content for m in request.messages)


class TestAsking:
    def test_a_question_is_queued_and_shown_as_it_will_be_sent(self):
        team = make_ask_team()

        response = team.ask()

        assert response.status_code == 202, response.text
        body = response.json()
        assert response.headers["location"] == f"/api/v1/questions/{body['id']}"
        assert body["status"] == "pending"
        assert body["position"] == 1
        assert MASKED_PLACE in body["question_sent"]
        assert SENSITIVE_PLACE not in json.dumps(body)
        assert body["masked"] == {"LOCATION": 1}
        assert body["answer"] is None

    def test_a_viewer_may_read_but_not_ask(self):
        team = make_ask_team()

        assert team.ask(browser=team.rt.viewer).status_code == 403

    def test_a_blank_question_is_refused(self):
        assert make_ask_team().ask("   ").status_code == 422

    def test_the_raw_question_is_never_stored(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]

        assert SENSITIVE_PLACE.encode() not in team.stored(question_id).question_sealed

    def test_asking_is_audited_with_ids_only(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]

        asked = [
            e
            for e in project_events(team.rt.harness, team.rt.project_id)
            if e.entry.action == "question.asked"
        ]
        assert [(e.entry.target_type, e.entry.target_id) for e in asked] == [
            ("question", question_id)
        ]


class TestAnswering:
    def test_the_worker_answers_with_claims_citing_retrieved_passages(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]

        assert team.answer_all() == 1

        body = team.question(question_id)
        assert body["status"] == "answered"
        claims = body["answer"]["claims"]
        assert claims[0]["citations"] == [{"tag": "P0001", "source_kind": "paper"}]
        assert body["answer"]["nothing_found"] is False
        assert [s["tag"] for s in body["answer"]["sources"]] == ["P0001"]
        assert body["answer"]["sources"][0]["passages"][0]["text"]

    def test_the_steps_behind_the_answer_are_kept(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]
        team.answer_all()

        steps = team.question(question_id)["answer"]["steps"]

        assert [(s["round"], s["role"]) for s in steps] == [
            (0, "retriever"),
            (1, "writer"),
            (1, "checks"),
            (1, "critic"),
        ]

    def test_only_masked_text_reaches_the_model(self):
        team = make_ask_team()
        team.ask()
        team.answer_all()

        sent = [sent_text(r) for r in team.provider.requests]
        assert sent
        assert all(SENSITIVE_PLACE not in text for text in sent)
        assert MASKED_PLACE in sent[0]

    def test_a_claim_citing_a_passage_never_retrieved_is_sent_back_for_revision(self):
        script = AnswerScript(
            writer=[answer(claim("Shelter kits arrived", "P0005")), good_answer()],
            critic=[critic_answer(), critic_answer()],
        )
        team = make_ask_team(script)
        question_id = team.ask().json()["id"]
        team.answer_all()

        body = team.question(question_id)["answer"]
        checks = [s for s in body["steps"] if s["role"] == "checks"]
        assert [len(c["problems"]) for c in checks] == [1, 0]
        assert body["claims"][0]["citations"][0]["tag"] == "P0001"
        assert body["kept_round"] == 2

    def test_nothing_relevant_is_an_answer_that_says_so_without_asking_the_model(self):
        team = make_ask_team(AnswerScript())
        question_id = team.ask("What do zebra crossings cost?").json()["id"]
        team.answer_all()

        body = team.question(question_id)["answer"]
        assert body["nothing_found"] is True
        assert body["message"] == NOTHING_FOUND
        assert body["claims"] == []
        assert team.provider.requests == []

    def test_a_review_with_no_evidence_has_nothing_to_answer_from(self):
        team = make_ask_team(AnswerScript(), passages=(), with_brief=False)
        question_id = team.ask().json()["id"]
        team.answer_all()

        assert team.question(question_id)["answer"]["nothing_found"] is True

    def test_an_unreachable_model_fails_the_question_in_plain_words(self):
        class Down:
            model_id = "qwen3.5:9b"

            async def chat(self, request: ChatRequest) -> ChatResult:
                raise InferenceUnavailableError("connection refused by 10.0.0.9:11434")

        team = make_ask_team(model=lambda script: Down())
        question_id = team.ask().json()["id"]
        team.answer_all()

        body = team.question(question_id)
        assert body["status"] == "failed"
        assert "model server" in body["error"]
        assert "10.0.0.9" not in json.dumps(body)


class TestThreads:
    def test_a_follow_up_carries_the_thread_masked(self):
        script = AnswerScript(
            writer=[good_answer(), good_answer()], critic=[critic_answer(), critic_answer()]
        )
        team = make_ask_team(script)
        first = team.ask().json()
        team.answer_all()

        follow_up = team.ask("And in larger towns?", thread_id=first["thread_id"])
        team.answer_all()

        assert follow_up.status_code == 202, follow_up.text
        assert (follow_up.json()["position"], follow_up.json()["thread_id"]) == (
            2,
            first["thread_id"],
        )
        writer = [r for r in team.provider.requests if "earlier" in sent_text(r)][-1]
        assert "Prices rose in small markets" in sent_text(writer)
        assert MASKED_PLACE in sent_text(writer)
        assert SENSITIVE_PLACE not in sent_text(writer)

    def test_a_thread_answers_one_question_at_a_time(self):
        team = make_ask_team()
        first = team.ask().json()

        second = team.ask("And in larger towns?", thread_id=first["thread_id"])

        assert second.status_code == 409

    def test_a_thread_of_another_review_is_404(self):
        team = make_ask_team()

        assert team.ask(thread_id=str(uuid.uuid4())).status_code == 404

    def test_a_reviews_threads_are_listed_newest_first_with_their_questions(self):
        script = AnswerScript(
            writer=[good_answer(), good_answer()], critic=[critic_answer(), critic_answer()]
        )
        team = make_ask_team(script)
        first = team.ask().json()
        team.answer_all()
        team.rt.clock.advance(minutes=1)
        second = team.ask("Do vouchers limit price risk?").json()

        threads = team.rt.viewer.get(f"/api/v1/reviews/{team.rt.review_id}/threads").json()

        assert [t["id"] for t in threads] == [second["thread_id"], first["thread_id"]]
        assert threads[1]["questions"][0]["status"] == "answered"

    def test_a_thread_is_read_with_every_question_in_order(self):
        team = make_ask_team()
        first = team.ask().json()

        thread = team.rt.viewer.get(f"/api/v1/threads/{first['thread_id']}").json()

        assert [q["id"] for q in thread["questions"]] == [first["id"]]

    def test_a_non_member_gets_the_same_404_as_for_a_missing_id(self):
        team = make_ask_team()
        first = team.ask().json()
        outsider = team.rt.outsider

        for path in (
            f"/api/v1/questions/{first['id']}",
            f"/api/v1/threads/{first['thread_id']}",
            f"/api/v1/reviews/{team.rt.review_id}/threads",
        ):
            existing = outsider.get(path)
            missing = outsider.get(path.replace(path.split("/")[4], str(uuid.uuid4())))
            assert existing.status_code == missing.status_code == 404
            assert existing.json() == missing.json()


class TestTheQueue:
    def test_a_question_whose_worker_died_is_answered_by_another(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]

        async def claim_and_die() -> None:
            async with team.rt.harness.providers.storage.unit_of_work() as uow:
                await uow.questions.claim_next(
                    "dead-worker", team.rt.clock(), timedelta(minutes=1)
                )
                await uow.commit()

        asyncio.run(claim_and_die())
        team.rt.clock.advance(minutes=2)
        team.answer_all()

        assert team.question(question_id)["status"] == "answered"

    def test_a_question_that_kills_its_worker_twice_is_given_up_plainly(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]

        async def claim_and_die() -> None:
            async with team.rt.harness.providers.storage.unit_of_work() as uow:
                await uow.questions.claim_next("dead", team.rt.clock(), timedelta(minutes=1))
                await uow.commit()

        for _ in range(2):
            asyncio.run(claim_and_die())
            team.rt.clock.advance(minutes=2)
        team.answer_all()

        body = team.question(question_id)
        assert (body["status"], body["error"]) == ("failed", INTERRUPTED)


class TestStoredSafely:
    def test_question_and_answer_are_unreadable_once_the_project_is_deleted(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]
        team.answer_all()
        record = team.stored(question_id)

        assert (
            send(team.rt.owner, "DELETE", f"{PROJECTS}/{team.rt.project_id}").status_code == 204
        )

        async def read() -> bytes:
            providers = team.rt.harness.providers
            async with providers.storage.unit_of_work() as uow:
                context = EncryptionContext(
                    UUID(team.rt.project_id), "questions.answer", question_id
                )
                assert record.answer_sealed is not None
                return await providers.encryption.decrypt(uow, context, record.answer_sealed)

        with pytest.raises(KeyDestroyedError):
            asyncio.run(read())

    def test_the_stored_answer_holds_no_text_in_the_clear(self):
        team = make_ask_team()
        question_id = team.ask().json()["id"]
        team.answer_all()

        sealed = team.stored(question_id).answer_sealed or b""
        assert b"Prices rose" not in sealed
        assert QUESTION.encode() not in sealed
