"""Helpers for the planning specs: a team with a review, a scripted model and a fake sanitiser.

Everything is real except the model and the sanitiser, and neither is a mock:
`ScriptedPlanner` validates every answer against the request's JSON schema, and
`FakeSanitizer` really masks known names and places with the domain's `apply_spans`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response

from app.application.inference import GuardedInference
from app.application.planning import NO_PROJECT_RULES, PlanService
from app.domain.inference import ChatRequest
from app.domain.masking import EntityType, MaskedText, MaskingRule, MaskSpan, apply_spans
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from tests.project_support import send
from tests.run_support import RunTeam, make_run_team

# What the fake sanitiser recognises. The run specs' question names Marib.
SENSITIVE: Mapping[str, EntityType] = {
    "Marib": EntityType.LOCATION,
    "Dr Amina Yusuf": EntityType.PERSON,
    "Aden": EntityType.LOCATION,
}

CLARIFYING = {
    "questions": [
        {"text": "Which outcomes matter most?", "suggestions": ["Food security", "Prices"]},
        {"text": "Which settings?", "suggestions": ["Camps", "Host communities"]},
        {"text": "Which years?", "suggestions": []},
    ]
}

DRAFT: dict[str, Any] = {
    "themes": [
        {
            "name": "intervention",
            "concepts": [{"term": "cash transfers", "synonyms": ["cash assistance"]}],
        },
        {
            "name": "outcome",
            "concepts": [{"term": "malnutrition", "synonyms": ["wasting"]}],
        },
    ],
    "criteria": {
        "inclusion": ["Studies of cash programmes in humanitarian settings"],
        "exclusion": ["Opinion pieces"],
    },
}

REVISED: dict[str, Any] = {
    "themes": [
        {
            "name": "intervention",
            "concepts": [
                {"term": "cash transfers", "synonyms": ["cash assistance", "mobile money"]}
            ],
        },
        {
            "name": "outcome",
            "concepts": [{"term": "malnutrition", "synonyms": ["wasting"]}],
        },
    ],
    "criteria": DRAFT["criteria"],
}

STRICT_TEXT = "[intervention]\ncash transfers; Cash-Based Assistance\n[outcome]\nwasting\n"


class FakeSanitizer:
    """Masks the `SENSITIVE` terms and any project rules, as the `Sanitizer` contract says."""

    def __init__(self, terms: Mapping[str, EntityType] = SENSITIVE) -> None:
        self._terms = terms
        self.inputs: list[str] = []

    async def mask(self, text: str, *, rules: Sequence[MaskingRule]) -> MaskedText:
        self.inputs.append(text)
        spans = [
            MaskSpan(start, start + len(term), kind, "fake@1")
            for term, kind in self._terms.items()
            for start in _occurrences(text, term)
        ]
        spans += [
            MaskSpan(start, start + len(rule.term), rule.entity_type, "fake@1")
            for rule in rules
            for start in _occurrences(text, rule.term)
        ]
        return apply_spans(text, spans)


def _occurrences(text: str, term: str) -> list[int]:
    found, start = [], text.find(term)
    while start != -1:
        found.append(start)
        start = text.find(term, start + 1)
    return found


@dataclass
class ScriptedPlanner:
    """Answers clarifying calls with `CLARIFYING` and drafting calls from `drafts`, in order.

    The last draft repeats once the list runs out. `failure`, when set, is raised
    instead of answering, the way an unreachable model server fails.
    """

    drafts: list[dict[str, Any]] = field(default_factory=lambda: [DRAFT, REVISED])
    clarifying: dict[str, Any] = field(default_factory=lambda: CLARIFYING)
    failure: Exception | None = None
    provider: FakeInferenceProvider = field(init=False)

    def __post_init__(self) -> None:
        self.provider = FakeInferenceProvider(self._answer, model_id="planner-test-model")

    def _answer(self, request: ChatRequest) -> Mapping[str, Any]:
        if self.failure is not None:
            raise self.failure
        if "questions" in request.output_schema["properties"]:
            return self.clarifying
        return self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]

    @property
    def requests(self) -> list[ChatRequest]:
        return self.provider.requests

    def sent_text(self) -> str:
        """Every message of every request, as the model server received them."""
        return "\n".join(m.content for r in self.requests for m in r.messages)


@dataclass
class PlanTeam:
    rt: RunTeam
    plans: PlanService
    model: ScriptedPlanner
    sanitizer: FakeSanitizer
    guard: FakePromptGuard

    @property
    def owner(self) -> TestClient:
        return self.rt.owner

    @property
    def reviewer(self) -> TestClient:
        return self.rt.reviewer

    @property
    def viewer(self) -> TestClient:
        return self.rt.viewer

    @property
    def outsider(self) -> TestClient:
        return self.rt.outsider

    @property
    def review_id(self) -> str:
        return self.rt.review_id

    def path(self, suffix: str = "") -> str:
        return f"/api/v1/reviews/{self.review_id}/plan{suffix}"

    def plan(self, browser: TestClient | None = None) -> dict[str, Any]:
        response = (browser or self.owner).get(self.path())
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def start(self, mode: str = "deep", browser: TestClient | None = None) -> Response:
        return send(browser or self.owner, "POST", self.path(), {"mode": mode})

    def answer(
        self,
        answers: Mapping[str, str | None],
        *,
        draft_now: bool = False,
        browser: TestClient | None = None,
    ) -> Response:
        body = {
            "answers": [{"question_id": q, "answer": a} for q, a in answers.items()],
            "draft_now": draft_now,
        }
        return send(browser or self.owner, "POST", self.path("/answers"), body)

    def change(
        self, request: str, based_on_version: int, browser: TestClient | None = None
    ) -> Response:
        body = {"request": request, "based_on_version": based_on_version}
        return send(browser or self.owner, "POST", self.path("/changes"), body)

    def accept(self, version: int, browser: TestClient | None = None) -> Response:
        return send(browser or self.owner, "POST", self.path("/accept"), {"version": version})

    def upload(
        self,
        content: str = STRICT_TEXT,
        format: str = "text",
        criteria: list[dict[str, str]] | None = None,
        browser: TestClient | None = None,
    ) -> Response:
        body: dict[str, Any] = {"format": format, "content": content}
        if criteria is not None:
            body["criteria"] = criteria
        return send(browser or self.owner, "PUT", self.path("/vocabulary"), body)

    def drafted(self, mode: str = "shallow") -> dict[str, Any]:
        """Start a plan that drafts at once, and return the plan."""
        response = self.start(mode)
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    def stored_bytes(self) -> bytes:
        """Every byte storage holds for plans: what a backup or a database copy contains."""
        state = self.rt.harness.storage.state.plan_state
        records = [
            *state.conversations.values(),
            *state.versions.values(),
            *state.receipts.values(),
        ]
        return b"\n".join(
            json.dumps(_plain(r), default=str).encode() + r.body_sealed for r in records
        )


def _plain(record: Any) -> dict[str, Any]:
    return {k: v for k, v in dataclasses.asdict(record).items() if k != "body_sealed"}


def make_plan_team(
    *,
    drafts: list[dict[str, Any]] | None = None,
    refusal: str | None = None,
) -> PlanTeam:
    rt = make_run_team()
    model = ScriptedPlanner() if drafts is None else ScriptedPlanner(drafts=drafts)
    sanitizer = FakeSanitizer()
    guard = FakePromptGuard(refusal)
    harness = rt.harness
    providers = harness.providers
    plans = PlanService(
        storage=providers.storage,
        encryption=providers.encryption,
        audit=providers.audit,
        inference=GuardedInference(model.provider, guard),
        sanitizer=sanitizer,
        rules=NO_PROJECT_RULES,
        clock=harness.clock,
    )
    harness.providers = dataclasses.replace(providers, plans=plans)
    harness.app.state.providers = harness.providers
    return PlanTeam(rt, plans, model, sanitizer, guard)


def stored_versions(team: PlanTeam) -> list[Any]:
    async def read() -> list[Any]:
        async with team.rt.harness.providers.storage.unit_of_work() as uow:
            return list(await uow.plans.list_versions(UUID(team.review_id)))

    return asyncio.run(read())
