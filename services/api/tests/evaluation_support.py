"""Helpers for the evaluation specs: a project with a brief, an admin in it, and a judge.

humani-t's answers come from the real follow-up worker of `question_support`, scripted
as there. The judge is a `FakeInferenceProvider` behind the real `GuardedInference`, and the
order the judge sees is scripted, so a spec can say which answer was A. The evaluation
service masks one place name, so a spec can prove what was stored and sent.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from httpx2 import Response

from app.application.evaluations import EvaluationService, EvaluationWorker
from app.application.inference import GuardedInference
from app.domain.evaluations import EvaluationRecord
from app.domain.inference import ChatRequest
from app.domain.masking import QUESTION_ENTITY_TYPES, EntityType, MaskingPolicy
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from app.privacy.fake import FakeDetector
from app.privacy.sanitizer import DetectorSanitizer
from tests.brief_support import SENSITIVE_PLACE, critic_answer
from tests.project_support import add_member, send
from tests.question_support import AnswerScript, AskTeam, good_answer, make_ask_team
from tests.support import ADMIN_EMAIL, MASKED_BY_DEPLOYMENT

EVALUATIONS = "/api/v1/admin/evaluations"
WRITER_MODEL = "qwen3.5:9b"
JUDGE_MODEL = "mistral-small:24b"

QUESTIONS = (
    f"Do cash transfers raise food prices in small markets near {SENSITIVE_PLACE}?",
    "Do cash transfers improve diet diversity where markets work?",
)
QUESTION_FILE = "\n".join(QUESTIONS)
OTHER_ANSWERS = (
    f"Yes: prices rose by a tenth in {SENSITIVE_PLACE} after transfers began.",
    "Diet diversity improved where traders could restock.",
)

_METRICS = (
    "coverage",
    "groundedness",
    "disagreements",
    "uncertainty",
    "humanitarian_awareness",
    "specificity",
)


def answers_file(*answers: str) -> str:
    return "\n".join(json.dumps({"answer": a}) for a in answers)


def metric_scores(value: int = 1, **overrides: int) -> dict[str, int]:
    return {**dict.fromkeys(_METRICS, value), **overrides}


def judge_output(
    a: Mapping[str, int] | None = None,
    b: Mapping[str, int] | None = None,
    winner: str = "a",
    reason: str = "A names the price effect and its size; B does not.",
) -> dict[str, Any]:
    return {
        "scores_a": dict(a or metric_scores()),
        "scores_b": dict(b or metric_scores()),
        "winner": winner,
        "reason": reason,
    }


class JudgeScript:
    """Answers the judge from a queue, then with a tie."""

    def __init__(self, outputs: Sequence[Mapping[str, Any]] = ()) -> None:
        self.queue = list(outputs)

    def __call__(self, request: ChatRequest) -> Mapping[str, Any]:
        return self.queue.pop(0) if self.queue else judge_output(winner="tie")


def user_document(request: ChatRequest) -> dict[str, Any]:
    """The judge's user message, which carries the question and answers as JSON."""
    document: dict[str, Any] = json.loads(request.messages[-1].content)
    return document


@dataclass
class EvaluationTeam:
    ask: AskTeam
    judge: FakeInferenceProvider
    worker: EvaluationWorker
    shows_humani_t_first: list[bool] = field(default_factory=list)

    @property
    def admin(self) -> Any:
        return self.ask.rt.team.admin

    @property
    def project_id(self) -> str:
        return self.ask.rt.project_id

    def body(
        self,
        *,
        question_file: str = QUESTION_FILE,
        systems: Sequence[tuple[str, str]] = (("System B", answers_file(*OTHER_ANSWERS)),),
        judge_model: str = JUDGE_MODEL,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "project_id": project_id or self.project_id,
            "judge_model": judge_model,
            "question_file": question_file,
            "other_systems": [{"name": n, "answers_file": f} for n, f in systems],
        }

    def create(self, browser: Any = None, **body: Any) -> Response:
        return send(browser or self.admin, "POST", EVALUATIONS, self.body(**body))

    def created(self, **body: Any) -> str:
        response = self.create(**body)
        assert response.status_code == 202, response.text
        evaluation_id: str = response.json()["id"]
        return evaluation_id

    def judge_all(self) -> int:
        async def drain() -> int:
            done = 0
            while await self.worker.judge_once():
                done += 1
            return done

        return asyncio.run(drain())

    def read(self, evaluation_id: str, browser: Any = None) -> dict[str, Any]:
        response = (browser or self.admin).get(f"{EVALUATIONS}/{evaluation_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def stored(self, evaluation_id: str) -> EvaluationRecord:
        async def read() -> EvaluationRecord | None:
            async with self.ask.rt.harness.providers.storage.unit_of_work() as uow:
                return await uow.evaluations.get(UUID(evaluation_id))

        record = asyncio.run(read())
        assert record is not None
        return record


def make_evaluation_team(
    judge: Callable[[ChatRequest], Mapping[str, Any]] | None = None,
    *,
    questions: int = len(QUESTIONS),
    masking_policy: MaskingPolicy = MASKED_BY_DEPLOYMENT,
    admin_role: str | None = "reviewer",
    is_on: bool = True,
) -> EvaluationTeam:
    script = AnswerScript(
        writer=[good_answer()] * questions, critic=[critic_answer()] * questions
    )
    ask = make_ask_team(script, masking_policy=masking_policy)
    rt = ask.rt
    if admin_role is not None:
        add_member(rt.owner, rt.project_id, ADMIN_EMAIL, admin_role)
    providers = rt.harness.providers
    sanitizer = DetectorSanitizer(
        (FakeDetector({SENSITIVE_PLACE: EntityType.LOCATION}),),
        entity_types=QUESTION_ENTITY_TYPES,
    )
    service = EvaluationService(
        storage=providers.storage,
        encryption=providers.encryption,
        audit=providers.audit,
        sanitizer=sanitizer,
        rules=providers.masking_rules,
        signals=providers.signals,
        writer_model=WRITER_MODEL,
        judge_models=(WRITER_MODEL, JUDGE_MODEL),
        clock=rt.clock,
    )
    rt.harness.providers = dataclasses.replace(
        providers, evaluations=service, is_evaluations_on=is_on
    )
    rt.harness.app.state.providers = rt.harness.providers
    provider = FakeInferenceProvider(judge or JudgeScript(), model_id=JUDGE_MODEL)
    orders: list[bool] = []
    worker = EvaluationWorker(
        storage=providers.storage,
        encryption=providers.encryption,
        answers=ask.worker,
        judges={JUDGE_MODEL: GuardedInference(provider, FakePromptGuard())},
        rules=providers.masking_rules,
        worker_id="evaluations-a",
        shows_humani_t_first=lambda: orders.pop(0) if orders else True,
        clock=rt.clock,
    )
    return EvaluationTeam(ask, provider, worker, orders)
