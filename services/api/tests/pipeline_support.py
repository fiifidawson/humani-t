"""Helpers for the pipeline specs: real stages, a real worker, and fakes for other lanes' parts.

Requests, storage, encryption, the audit log and the worker are real. The plan, masking,
paper sources and model are fakes in the cross-lane contract's shape: `FakePlans`,
`FakeMaskStage` and `FakeMaskedTexts`, `CorpusSource` (evaluates queries for real) and
`FakeInferenceProvider` behind the real `GuardedInference`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.inference import GuardedInference
from app.application.papers import PaperService, PaperStore, ScreenedEvidence
from app.application.pipeline import DiscoverStage, ExportStage, IngestStage
from app.application.screening import ScreenStage
from app.domain.inference import ChatRequest, ChatResult, InferenceUnavailableError
from app.domain.paper import PaperRecord
from app.domain.plans import (
    Criterion,
    CriterionKind,
    PlanAuthor,
    ReviewPlan,
    first_plan,
    number_criteria,
)
from app.domain.query import BooleanQuery
from app.domain.runs import JsonValue
from app.domain.sources import PaperSourceUnavailableError
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from app.runs.base import Stage, StageContext
from app.search.base import DEFAULT_LIMIT
from app.search.fake import FakePaperSearchProvider
from app.storage.base import UnitOfWork
from tests.run_support import RunTeam, make_run_team

# Sensitive on purpose: a place name the fake masker hides. It may appear in
# stored, sealed text, and never in anything sent to a model or to a member.
PLACE = "Marib"
MASKED_PLACE = "[LOCATION_1]"

CORPUS: tuple[PaperRecord, ...] = (
    PaperRecord(
        source_id="first",
        external_id="f-1",
        title="Cash transfers and acute malnutrition among displaced children",
        year=2023,
        doi="10.1000/cash.1",
        is_open_access=True,
        abstract=f"Unconditional cash for displaced households in {PLACE} cut malnutrition.",
        url="https://example.org/f-1",
    ),
    PaperRecord(
        source_id="first",
        external_id="f-2",
        title="Food vouchers in displacement camps",
        year=2021,
        doi="10.1000/voucher.2",
        abstract="Vouchers for displaced families; malnutrition measured at six months.",
    ),
    PaperRecord(
        source_id="first",
        external_id="f-3",
        title="Cash transfers in stable urban markets",
        year=2020,
        abstract="Cash and prices in a non-crisis city; malnutrition was not an outcome.",
    ),
    PaperRecord(
        source_id="first",
        external_id="f-4",
        title="Cash transfers and malnutrition: a trial protocol",
        year=2024,
        doi="10.1000/protocol.4",
    ),
)

SECOND_CORPUS: tuple[PaperRecord, ...] = (
    # The same paper as f-1, found by DOI in another spelling.
    PaperRecord(
        source_id="second",
        external_id="s-1",
        title="CASH TRANSFERS AND ACUTE MALNUTRITION AMONG DISPLACED CHILDREN",
        year=2023,
        doi="https://doi.org/10.1000/CASH.1",
        abstract="A different abstract the merge must not keep.",
    ),
    PaperRecord(
        source_id="second",
        external_id="s-2",
        title="Mobile money and malnutrition after displacement",
        year=2022,
        doi="10.1000/mobile.5",
        abstract="Cash by mobile money for displaced households; malnutrition fell.",
    ),
)

VOCABULARY = Vocabulary(
    concepts=(
        Concept("cash", ("voucher", "mobile money"), theme="intervention"),
        Concept("malnutrition", theme="outcome"),
    ),
    origin=VocabularyOrigin.USER_SUPPLIED,
    version=3,
)
PLANNED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


CRITERIA = number_criteria(
    [
        (CriterionKind.INCLUSION, "Displaced people or refugees"),
        (CriterionKind.INCLUSION, "Measures malnutrition"),
        (CriterionKind.EXCLUSION, "Not a humanitarian or crisis setting"),
    ]
)


def accepted_plan(
    review_id: str, *, criteria: tuple[Criterion, ...] = CRITERIA, version: int = 3
) -> ReviewPlan:
    """A real `ReviewPlan`, written by a person and accepted, as the plan lane stores it."""
    person = PlanAuthor.person(UUID(int=7))
    plan = first_plan(
        UUID(review_id),
        concepts=VOCABULARY.concepts,
        criteria=criteria,
        origin=VOCABULARY.origin,
        author=person,
        at=PLANNED_AT,
        version=version,
    )
    return plan.accepted(UUID(int=7), PLANNED_AT)


class FakePlans:
    """`PlanSource`: the accepted plan per review, or `None`."""

    def __init__(self) -> None:
        self.plans: dict[UUID, ReviewPlan] = {}

    def accept(self, review_id: str, plan: ReviewPlan | None = None) -> None:
        self.plans[UUID(review_id)] = plan or accepted_plan(review_id)

    async def accepted_plan(self, uow: UnitOfWork, review_id: UUID) -> ReviewPlan | None:
        return self.plans.get(review_id)


class FakeMaskedTexts:
    """`MaskedTextSource`, filled by `FakeMaskStage`."""

    def __init__(self) -> None:
        self.texts: dict[tuple[UUID, UUID], str] = {}

    async def masked_text(
        self, uow: UnitOfWork, project_id: UUID, paper_id: UUID
    ) -> str | None:
        return self.texts.get((project_id, paper_id))


class FakeMaskStage:
    """What the mask lane's stage does, as far as this lane can tell: read, mask, store."""

    def __init__(self, rt: RunTeam, store: PaperStore, masked: FakeMaskedTexts) -> None:
        self._rt = rt
        self._store = store
        self._masked = masked
        self.skip: set[str] = set()

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._rt.harness.providers.storage.unit_of_work() as uow:
            texts = await self._store.texts_for_run(uow, context.project_id, context.run_id)
        for text in texts:
            if any(marker in text.text for marker in self.skip):
                continue
            self._masked.texts[(context.project_id, text.paper_id)] = text.text.replace(
                PLACE, MASKED_PLACE
            )
        return {"masked": len(texts)}


class CorpusSource:
    """A `PaperSearchProvider` with its own id, evaluating queries for real over a corpus."""

    def __init__(self, source_id: str, corpus: Sequence[PaperRecord]) -> None:
        self._source_id = source_id
        self._inner = FakePaperSearchProvider([replace(r, source_id=source_id) for r in corpus])
        self.calls: list[BooleanQuery] = []

    @property
    def source_id(self) -> str:
        return self._source_id

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        self.calls.append(query)
        return await self._inner.search(query, limit)


class FailingSource:
    """A source that cannot be searched: down, refusing, or answering unreadably."""

    def __init__(self, source_id: str = "down") -> None:
        self._source_id = source_id
        self.calls = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        self.calls += 1
        raise PaperSourceUnavailableError(self._source_id, "HTTP 503 from 10.0.0.7")


def criteria_ids_in(request: ChatRequest) -> list[str]:
    """The criterion ids the screening schema allows, as the model is offered them."""
    items = request.output_schema["properties"]["criteria_matched"]["items"]
    return list(items["enum"])


def paper_text_in(request: ChatRequest) -> str:
    return request.messages[-1].content


def answer(
    label: str, priority: str = "medium", matched: Sequence[str] = (), score: float = 0.8
) -> dict[str, Any]:
    return {
        "label": label,
        "score": score,
        "priority": priority,
        "rationale": f"Decided {label}: the abstract says enough.\nSecond line.",
        "criteria_matched": list(matched),
    }


def reasonable_answer(request: ChatRequest) -> dict[str, Any]:
    """What a sensible model says about the corpus above, in the prompt's own criterion ids."""
    text = paper_text_in(request)
    if "stable urban" in text:
        return with_answers(request, answer("exclude", "low", ["I2", "E1"]))
    if "Vouchers" in text:
        return with_answers(request, answer("include", "medium", ["I1", "I2"]))
    if "displaced" in text.lower():
        return with_answers(request, answer("include", "high", ["I1", "I2"], score=0.95))
    return with_answers(request, answer("uncertain", "low"))


def with_answers(
    request: ChatRequest, base: dict[str, Any], quote_from: str | None = None
) -> dict[str, Any]:
    """`base` plus an answer for every criterion, quoting the text it was sent where met.

    The quote is an excerpt, as a real one is: never the whole abstract.
    """
    text = paper_text_in(request)
    quote = quote_from if quote_from is not None else text.split(";")[0].split(".")[0][:40]
    return {
        **base,
        "answers": [
            {
                "id": criterion,
                "answer": "yes" if criterion in base["criteria_matched"] else "no",
                "quote": quote if criterion in base["criteria_matched"] else None,
            }
            for criterion in criteria_ids_in(request)
        ],
    }


class ObservedInference:
    """A model that takes a moment to answer, so concurrent calls really overlap."""

    def __init__(
        self,
        script: Callable[[ChatRequest], Mapping[str, Any]],
        *,
        fail_with: BaseException | None = None,
        fail_after: int | None = None,
    ) -> None:
        self._inner = FakeInferenceProvider(script, model_id="llama3.1:8b")
        self._fail_with = fail_with
        self._fail_after = fail_after
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def requests(self) -> list[ChatRequest]:
        return self._inner.requests

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    async def chat(self, request: ChatRequest) -> ChatResult:
        if self._fail_after is not None and len(self.requests) >= self._fail_after:
            assert self._fail_with is not None
            raise self._fail_with
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            for _ in range(3):
                await asyncio.sleep(0)
            return await self._inner.chat(request)
        finally:
            self.in_flight -= 1


def model_down() -> InferenceUnavailableError:
    return InferenceUnavailableError("connection refused by 10.0.0.9:11434")


@dataclass
class Pipeline:
    """A run team plus every real pipeline stage, wired to fakes for the other lanes."""

    rt: RunTeam
    plans: FakePlans
    masked: FakeMaskedTexts
    store: PaperStore
    sources: list[Any]
    model: ObservedInference
    guard: FakePromptGuard
    mask: FakeMaskStage
    concurrency: int = 2
    extra: dict[str, Stage] = field(default_factory=dict)

    def stages(self) -> dict[str, Stage]:
        providers = self.rt.harness.providers
        storage, clock = providers.storage, self.rt.clock
        inference = GuardedInference(self.model, self.guard)
        stages: dict[str, Stage] = {
            "discover": DiscoverStage(
                storage=storage,
                store=self.store,
                plans=self.plans,
                sources=self.sources,
                clock=clock,
            ),
            "ingest": IngestStage(storage=storage, store=self.store, clock=clock),
            "mask": self.mask,
            "screen": ScreenStage(
                storage=storage,
                store=self.store,
                plans=self.plans,
                inference=inference,
                masked=self.masked,
                rules=providers.masking_rules,
                audit=providers.audit,
                concurrency=self.concurrency,
                clock=clock,
            ),
            "export": ExportStage(storage=storage, store=self.store),
        }
        stages.update(self.extra)
        return stages

    @property
    def evidence(self) -> ScreenedEvidence:
        return ScreenedEvidence(self.store, self.masked)

    @property
    def service(self) -> PaperService:
        providers = self.rt.harness.providers
        return PaperService(
            storage=providers.storage,
            store=self.store,
            masked=self.masked,
            encryption=providers.encryption,
        )

    def summary(self, run_id: str, stage: str) -> dict[str, Any]:
        finished = [
            e.data
            for e in self.rt.events(run_id)
            if e.name == "stage.finished" and e.data["stage"] == stage
        ]
        assert finished, f"{stage} did not finish: {self.failure(run_id)}"
        summary: dict[str, Any] = finished[-1]["summary"]
        return summary

    def failure(self, run_id: str) -> str | None:
        ended = [e.data for e in self.rt.events(run_id) if e.name == "job.finished"]
        return ended[-1]["error"] if ended else None

    def progress(self, run_id: str, stage: str) -> list[dict[str, Any]]:
        return [
            e.data
            for e in self.rt.events(run_id)
            if e.name == "stage.progress" and e.data["stage"] == stage
        ]


def make_pipeline(
    *,
    sources: Sequence[Any] | None = None,
    script: Callable[[ChatRequest], Mapping[str, Any]] = reasonable_answer,
    concurrency: int = 2,
    accept_plan: bool = True,
) -> Pipeline:
    rt = make_run_team()
    providers = rt.harness.providers
    store = PaperStore(providers.encryption, rt.clock)
    masked = FakeMaskedTexts()
    plans = FakePlans()
    if accept_plan:
        plans.accept(rt.review_id)
    pipeline = Pipeline(
        rt=rt,
        plans=plans,
        masked=masked,
        store=store,
        sources=list(
            sources
            if sources is not None
            else [CorpusSource("first", CORPUS), CorpusSource("second", SECOND_CORPUS)]
        ),
        model=ObservedInference(script),
        guard=FakePromptGuard(),
        mask=FakeMaskStage(rt, store, masked),
        concurrency=concurrency,
    )
    # The routes read masked text through the same fake the stages do.
    rt.harness.providers = dataclasses.replace(providers, papers=pipeline.service)
    rt.harness.app.state.providers = rt.harness.providers
    return pipeline


ALL_STAGES = ("discover", "ingest", "mask", "screen", "export")


def contains(value: object, needle: str) -> bool:
    """Whether `needle` appears anywhere in a JSON-shaped value, keys included."""
    return needle in json.dumps(value)


def tag_number(tag: str) -> int:
    match = re.fullmatch(r"P(\d+)", tag)
    assert match, tag
    return int(match.group(1))
