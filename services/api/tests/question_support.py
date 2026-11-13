"""Helpers for the follow-up question specs: a scripted model and a team with a brief.

`AnswerScript` answers the answer Writer and the Critic from their own queues,
telling them apart by the schema each request asks for. The question service
is rebuilt with a sanitiser that masks one place name, so a spec can prove
which text was stored and sent. Everything else is real: routes, storage,
encryption, audit, the worker and BM25.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.application.answer_loop import ANSWER_SCHEMA
from app.application.brief_loop import CRITIC_SCHEMA
from app.application.inference import GuardedInference
from app.application.questions import QuestionService, QuestionWorker
from app.briefs.bm25 import Bm25Retriever
from app.briefs.fake import FakeEvidenceSource
from app.domain.inference import ChatRequest
from app.domain.masking import QUESTION_ENTITY_TYPES, EntityType, MaskingPolicy
from app.domain.questions import QuestionRecord
from app.domain.screening import Passage
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from app.privacy.fake import FakeDetector
from app.privacy.sanitizer import DetectorSanitizer
from tests.brief_support import CORPUS, SENSITIVE_PLACE, claim, critic_answer, write_brief
from tests.project_support import send
from tests.run_support import RunTeam, make_run_team
from tests.support import MASKED_BY_DEPLOYMENT

QUESTION = f"Do cash transfers raise food prices in small markets near {SENSITIVE_PLACE}?"


def answer(*claims: dict[str, Any]) -> dict[str, Any]:
    return {"claims": list(claims)}


def good_answer() -> dict[str, Any]:
    return answer(claim("Prices rose in small markets after cash transfers", "P0001"))


def _same(schema: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    return json.loads(json.dumps(dict(schema))) == json.loads(json.dumps(dict(other)))


class AnswerScript:
    def __init__(
        self,
        *,
        writer: Sequence[Mapping[str, Any]] = (),
        critic: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.queues: dict[str, list[Mapping[str, Any]]] = {
            "writer": list(writer),
            "critic": list(critic),
        }

    def __call__(self, request: ChatRequest) -> Mapping[str, Any]:
        if _same(request.output_schema, ANSWER_SCHEMA):
            role = "writer"
        elif _same(request.output_schema, CRITIC_SCHEMA):
            role = "critic"
        else:
            raise AssertionError("a request asked for a schema no answer role uses")
        queue = self.queues[role]
        assert queue, f"the {role} was asked more often than the spec scripted"
        return queue.pop(0)


def answered_first_time() -> AnswerScript:
    return AnswerScript(writer=[good_answer()], critic=[critic_answer()])


@dataclass
class AskTeam:
    rt: RunTeam
    provider: FakeInferenceProvider
    script: AnswerScript
    worker: QuestionWorker

    def ask(
        self, question: str = QUESTION, thread_id: str | None = None, browser: Any = None
    ) -> Any:
        body: dict[str, Any] = {"question": question}
        if thread_id is not None:
            body["thread_id"] = thread_id
        return send(
            browser or self.rt.reviewer,
            "POST",
            f"/api/v1/reviews/{self.rt.review_id}/questions",
            body,
        )

    def answer_all(self) -> int:
        async def drain() -> int:
            done = 0
            while await self.worker.answer_once():
                done += 1
            return done

        return asyncio.run(drain())

    def question(self, question_id: str, browser: Any = None) -> dict[str, Any]:
        response = (browser or self.rt.viewer).get(f"/api/v1/questions/{question_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def stored(self, question_id: str) -> QuestionRecord:
        async def read() -> QuestionRecord | None:
            async with self.rt.harness.providers.storage.unit_of_work() as uow:
                return await uow.questions.get(UUID(question_id))

        record = asyncio.run(read())
        assert record is not None
        return record


def make_ask_team(
    script: AnswerScript | None = None,
    *,
    passages: Sequence[Passage] = CORPUS,
    with_brief: bool = True,
    model: Callable[[AnswerScript], Any] | None = None,
    worker_id: str = "answers-a",
    masking_policy: MaskingPolicy = MASKED_BY_DEPLOYMENT,
) -> AskTeam:
    rt = make_run_team(masking_policy)
    if with_brief:
        write_brief(rt)
    providers = rt.harness.providers
    script = script or answered_first_time()
    provider = FakeInferenceProvider(script, model_id="qwen3.5:9b")
    sanitizer = DetectorSanitizer(
        (FakeDetector({SENSITIVE_PLACE: EntityType.LOCATION}),),
        entity_types=QUESTION_ENTITY_TYPES,
    )
    questions = QuestionService(
        storage=providers.storage,
        encryption=providers.encryption,
        audit=providers.audit,
        sanitizer=sanitizer,
        rules=providers.masking_rules,
        signals=providers.signals,
        clock=rt.clock,
    )
    rt.harness.providers = dataclasses.replace(providers, questions=questions)
    rt.harness.app.state.providers = rt.harness.providers
    worker = QuestionWorker(
        storage=providers.storage,
        encryption=providers.encryption,
        inference=GuardedInference(model(script) if model else provider, FakePromptGuard()),
        retriever=Bm25Retriever(),
        evidence=FakeEvidenceSource(passages),
        rules=providers.masking_rules,
        worker_id=worker_id,
        clock=rt.clock,
    )
    return AskTeam(rt, provider, script, worker)
