"""Scripted model answers for the brief specs: a planner, a writer and a critic.

`RoleScript` is the callable a `FakeInferenceProvider` is built with. It tells
the roles apart by the JSON schema each request asks for, and answers each from
its own queue, so a spec scripts "the writer's second draft" without counting
calls. The fake provider validates every answer against the schema for real,
so a scripted malformed answer fails the way production would.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.application.brief_loop import CRITIC_SCHEMA, PLANNER_SCHEMA, WRITER_SCHEMA
from app.application.briefs import AnswerStage
from app.application.inference import GuardedInference
from app.briefs.bm25 import Bm25Retriever
from app.briefs.fake import FakeEvidenceSource
from app.domain.briefs import BriefRecord, SourceDetails, SourceKind
from app.domain.inference import ChatRequest
from app.domain.masking import QUESTION_ENTITY_TYPES, EntityType
from app.domain.screening import Passage
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from app.privacy.fake import FakeDetector
from app.privacy.sanitizer import DetectorSanitizer
from tests.run_support import RunTeam, run_until_idle

OPTIONS = ("Cash transfers", "In-kind food")


def paper_id(n: int) -> UUID:
    return UUID(int=n)


def _passage(n: int, text: str) -> Passage:
    return Passage(f"P000{n}", paper_id(n), SourceKind.PAPER, text, "abstract")


CORPUS = (
    _passage(1, "Cash transfers raised staple food prices in small markets near [LOCATION_1]."),
    _passage(2, "In-kind food baskets reduced acute malnutrition among displaced children."),
    _passage(3, "Cash transfers improved diet diversity where markets worked."),
    _passage(4, "Protection risks rose for women carrying cash in camps."),
    _passage(5, "Earthquake shelter kits were distributed after the floods."),
)

SUB_QUESTIONS = ("cash transfers food prices markets", "in-kind food malnutrition children")


def claim(text: str, *tags: str) -> dict[str, Any]:
    return {
        "text": text,
        "support": "evidence" if tags else "hypothesis",
        "citations": list(tags),
    }


def planner_answer(**changes: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "decision": "Choose between cash and in-kind food in camps near [LOCATION_1]",
        "options": [
            {"name": n, "description": f"{n} for displaced families."} for n in OPTIONS
        ],
        "criteria": ["Do no harm", "Food security", "Cost to deliver"],
        "sub_questions": list(SUB_QUESTIONS),
    }
    answer.update(changes)
    return answer


def writer_answer(*, cite: str = "P0001", **changes: Any) -> dict[str, Any]:
    """A valid draft. `cite` is the tag its first benefit cites."""
    answer: dict[str, Any] = {
        "bottom_line": [
            claim("Cash is likely to improve food security where markets work.", "P0003"),
            claim("Evidence for camps with weak markets is thin."),
        ],
        "options": [
            {
                "name": "Cash transfers",
                "description": "Unconditional cash, by mobile money where possible.",
                "strength": "moderate",
                "benefits": [claim("Better diet diversity", cite)],
                "risks": [claim("Prices can rise in small markets", "P0001")],
            },
            {
                "name": "In-kind food",
                "description": "A fixed food basket.",
                "strength": "weak",
                "benefits": [claim("Less acute malnutrition", "P0002")],
                "risks": [claim("Households cannot choose what they need")],
            },
        ],
        "trade_offs": [
            {
                "criterion": "Do no harm",
                "assessments": [
                    {"option": "Cash transfers", "claim": claim("Protection risks", "P0004")},
                    {"option": "In-kind food", "claim": claim("No cash is carried")},
                ],
            },
            {
                "criterion": "Food security",
                "assessments": [
                    {"option": "Cash transfers", "claim": claim("Diet diversity", "P0003")},
                    {"option": "In-kind food", "claim": claim("Malnutrition falls", "P0002")},
                ],
            },
        ],
        "conflicts": [
            {
                "question": "Do cash transfers raise local food prices?",
                "type": "context",
                "positions": [
                    claim("Yes, in small markets", "P0001"),
                    claim("Not where markets work", "P0003"),
                ],
                "meaning": claim("The effect likely depends on market size."),
            }
        ],
        "considerations": [
            {
                "criterion": "Do no harm",
                "option": "In-kind food",
                "because": claim("Recipients carry no cash", "P0004"),
            }
        ],
        "gaps": ["Nothing on people with disabilities."],
    }
    answer.update(changes)
    return answer


SCORES = {
    "coverage": 2,
    "groundedness": 2,
    "disagreements": 1,
    "uncertainty": 2,
    "humanitarian_awareness": 2,
    "specificity": 1,
}


def critic_answer(
    verdict: str = "accept",
    *,
    queries: Sequence[str] = (),
    issues: Sequence[str] = (),
    scores: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "scores": dict(scores or SCORES),
        "issues": list(issues),
        "queries": list(queries),
    }


INVALID = {"nonsense": True}

_ROLES = (("planner", PLANNER_SCHEMA), ("writer", WRITER_SCHEMA), ("critic", CRITIC_SCHEMA))


def role_of(request: ChatRequest) -> str:
    for role, schema in _ROLES:
        if dict(request.output_schema) == json.loads(json.dumps(schema)):
            return role
    raise AssertionError("a request asked for a schema no brief role uses")


class RoleScript:
    """Answers each role from its own queue; raises if a role is asked once too often."""

    def __init__(
        self,
        *,
        planner: Sequence[Mapping[str, Any]] = (),
        writer: Sequence[Mapping[str, Any]] = (),
        critic: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.queues: dict[str, list[Mapping[str, Any]]] = {
            "planner": list(planner),
            "writer": list(writer),
            "critic": list(critic),
        }
        self.asked: list[str] = []

    def __call__(self, request: ChatRequest) -> Mapping[str, Any]:
        role = role_of(request)
        self.asked.append(role)
        queue = self.queues[role]
        assert queue, f"the {role} was asked more often than the spec scripted"
        return queue.pop(0)


def accepted_first_time() -> RoleScript:
    return RoleScript(
        planner=[planner_answer()], writer=[writer_answer()], critic=[critic_answer()]
    )


def text_sent(request: ChatRequest) -> str:
    return "\n".join(m.content for m in request.messages)


SENSITIVE_PLACE = "Marib"
MASKED_PLACE = "[LOCATION_1]"

DETAILS = {
    paper_id(1): SourceDetails(
        title="Cash transfers and food prices in small markets",
        year=2023,
        doi="10.1016/j.foodpol.2023.001",
        url="https://doi.org/10.1016/j.foodpol.2023.001",
        screening_label="include",
        rationale="Reports price effects of cash transfers in camp markets.",
    ),
}


@dataclass
class BriefRig:
    """An answer stage over a run team's storage, with a scripted model behind it."""

    stage: AnswerStage
    provider: FakeInferenceProvider
    evidence: FakeEvidenceSource
    script: RoleScript


def answer_stage(
    rt: RunTeam,
    script: RoleScript | None = None,
    *,
    passages: Sequence[Passage] = CORPUS,
    max_rounds: int = 3,
    guard: FakePromptGuard | None = None,
    before_answer: Callable[[ChatRequest], None] | None = None,
) -> BriefRig:
    """The real answer stage, wired as `api/dependencies.py` wires it, with fakes at the seams.

    `before_answer` sees each request before the script answers it, so a spec
    can cancel the run or crash the worker in the middle of the loop.
    """
    providers = rt.harness.providers
    roles = script or accepted_first_time()

    def answer(request: ChatRequest) -> Mapping[str, Any]:
        if before_answer is not None:
            before_answer(request)
        return roles(request)

    provider = FakeInferenceProvider(answer, model_id="qwen3.5:9b")
    evidence = FakeEvidenceSource(passages, DETAILS)
    stage = AnswerStage(
        storage=providers.storage,
        encryption=providers.encryption,
        audit=providers.audit,
        inference=GuardedInference(provider, guard or FakePromptGuard()),
        retriever=Bm25Retriever(),
        evidence=evidence,
        catalogue=evidence,
        sanitizer=DetectorSanitizer(
            (FakeDetector({SENSITIVE_PLACE: EntityType.LOCATION}),),
            entity_types=QUESTION_ENTITY_TYPES,
        ),
        rules=providers.masking_rules,
        instructions=providers.model_instructions,
        max_rounds=max_rounds,
        clock=rt.clock,
    )
    return BriefRig(stage, provider, evidence, roles)


def write_brief(rt: RunTeam, rig: BriefRig | None = None) -> str:
    """Run the answer stage to the end through a real worker; return the run id."""
    rig = rig or answer_stage(rt)
    run_id = rt.start_run(["answer"])
    run_until_idle(rt.worker({"answer": rig.stage}))
    return run_id


def stored_brief(rt: RunTeam, run_id: str) -> BriefRecord | None:
    async def read() -> BriefRecord | None:
        async with rt.harness.providers.storage.unit_of_work() as uow:
            return await uow.briefs.for_run(UUID(run_id))

    return asyncio.run(read())
