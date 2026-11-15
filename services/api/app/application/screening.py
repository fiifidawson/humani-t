"""The `screen` stage: ask the model about each paper, using its masked text only.

Every call goes through `GuardedInference`, and a paper with no masked text is skipped.
Each decision is stored as it is made, with the model, prompt and plan version it used,
so a retry redoes only unfinished papers. A bad answer is asked again once, then marked
`uncertain` for a person to check. The project's screening instructions follow the
built-in prompt, as they stood when the run was started.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from app.application.audit import SYSTEM, AuditLog
from app.application.inference import GuardedInference
from app.application.masking import MaskingRuleService
from app.application.model_instructions import ModelInstructionsService
from app.application.papers import PaperStore
from app.application.pipeline import (
    accepted_plan_or_fail,
    adopted_or_fail,
    adoption_facts,
    held_run,
)
from app.domain.discovery import RunPaper, TextKind
from app.domain.inference import (
    ChatMessage,
    ChatRequest,
    ChatRole,
    InferenceRateLimitedError,
    InferenceUnavailableError,
    InvalidStructuredOutputError,
    PromptRefusedError,
)
from app.domain.masking import MaskingRule
from app.domain.model_instructions import InstructionsKind, SentInstructions, with_instructions
from app.domain.plans import (
    CriterionKind,
    PlanSource,
    ReviewPlan,
    plan_text_fields,
    refuse_rule_terms,
)
from app.domain.runs import JsonValue, Run
from app.domain.screening import (
    MAX_QUOTE_LENGTH,
    CriterionAnswer,
    CriterionVerdict,
    MaskedTextSource,
    Priority,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
    exclusion_reasons,
    screened_item,
)
from app.runs.base import StageContext, StageFailedError
from app.storage.base import StorageProvider, UnitOfWork

SCREEN_PROMPT_VERSION = "screen-3"
DEFAULT_CONCURRENCY = 2

NO_CRITERIA = (
    "The accepted plan has no screening criteria. Add some to the plan, then run again."
)
NOTHING_MASKED = (
    "None of the papers' text has been masked yet, so none was sent to the model. Run "
    "the Mask stage, then Screen again."
)
MODEL_DOWN = (
    "The model server isn't answering. Papers screened so far are kept; try the run "
    "again once it is back."
)
MODEL_RATE_LIMITED = (
    "The model service's rate limit was reached. Papers screened so far are kept; "
    "wait a minute, then run it again."
)

_SYSTEM_PROMPT = """You screen research papers for a humanitarian evidence review.
Decide from the paper's title and abstract alone whether it meets the review's criteria.

Inclusion criteria (an included paper meets them):
{inclusion}

Exclusion criteria (a paper that meets any of them is excluded):
{exclusion}

Answer with JSON only:
- "label": "include", "exclude", or "uncertain" when the abstract does not say enough;
- "criteria_matched": the ids of every criterion above that the paper meets,
  inclusion and exclusion alike;
- "priority": "high" if it clearly meets every inclusion criterion, "medium" if it
  meets them with some doubt, "low" if only in part;
- "score": your confidence in the label, from 0 to 1;
- "rationale": one or two plain sentences saying why, naming the criteria in words;
- "answers": one entry per criterion above, with its "id", an "answer" ("yes",
  "partly", "no" or "unclear"; yes or partly exactly for the criteria matched) and
  a "quote": the words of the text that show it, copied exactly, or null.

Some words in the text are replaced by placeholders such as [PERSON_1] or
[LOCATION_1]. Treat each as the word it hides; never guess what it was."""

_MAX_RATIONALE = 1000
_PRIVACY_STOPPED = "the privacy check stopped this paper's text"
_MAX_REASON = 140
_MAX_TITLE = 80
_WHITESPACE = re.compile(r"\s+")

_LABEL_CODES = {
    ScreeningTier.HIGHLY_RELEVANT: "HIGH",
    ScreeningTier.RELEVANT: "MEDIUM",
    ScreeningTier.POSSIBLY_RELEVANT: "LOW",
    ScreeningTier.EXCLUDED: "EXCLUDE",
    ScreeningTier.UNCERTAIN: "UNCERTAIN",
}

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def screening_schema(criterion_ids: Sequence[str]) -> dict[str, Any]:
    """The JSON schema every screening answer must match."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "score", "priority", "rationale", "criteria_matched", "answers"],
        "properties": {
            "label": {"enum": [label.value for label in ScreeningLabel]},
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "priority": {"enum": [priority.value for priority in Priority]},
            "rationale": {"type": "string", "minLength": 1, "maxLength": _MAX_RATIONALE},
            "criteria_matched": {
                "type": "array",
                "items": {"enum": list(criterion_ids)},
                "uniqueItems": True,
            },
            # One per criterion, so a reviewer sees every criterion's verdict. A
            # model told this exactly (strict structured output) cannot skip one.
            "answers": {
                "type": "array",
                "minItems": len(criterion_ids),
                "maxItems": len(criterion_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "answer", "quote"],
                    "properties": {
                        "id": {"enum": list(criterion_ids)},
                        "answer": {"enum": [v.value for v in CriterionVerdict]},
                        "quote": {
                            "anyOf": [
                                # No maxLength: a longer quote is shortened, not refused.
                                {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": (
                                        f"Cut to {MAX_QUOTE_LENGTH} characters when longer."
                                    ),
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
            },
        },
    }


def screening_request(
    plan: ReviewPlan, masked_text: str, instructions: str = ""
) -> ChatRequest:
    """The call for one paper; the project's `instructions` follow the built-in prompt."""

    def listed(kind: CriterionKind) -> str:
        lines = [f"{c.id}: {c.text}" for c in plan.criteria if c.kind is kind]
        return "\n".join(lines) or "(none)"

    system = _SYSTEM_PROMPT.format(
        inclusion=listed(CriterionKind.INCLUSION), exclusion=listed(CriterionKind.EXCLUSION)
    )
    return ChatRequest(
        messages=(
            ChatMessage(ChatRole.SYSTEM, with_instructions(system, instructions)),
            ChatMessage(ChatRole.USER, masked_text),
        ),
        output_schema=screening_schema([c.id for c in plan.criteria]),
        temperature=0.0,
    )


def one_line(text: str, limit: int) -> str:
    """The first line of `text`, its whitespace collapsed, cut to `limit` characters."""
    first = _WHITESPACE.sub(" ", text.strip().splitlines()[0] if text.strip() else "").strip()
    return first if len(first) <= limit else f"{first[: limit - 1].rstrip()}…"


@dataclass(frozen=True, slots=True)
class _Screening:
    """What every call of one execution shares: the plan and the instructions as sent."""

    plan: ReviewPlan
    instructions: SentInstructions | None

    def request(self, masked_text: str) -> ChatRequest:
        text = "" if self.instructions is None else self.instructions.text
        return screening_request(self.plan, masked_text, text)


@dataclass(frozen=True, slots=True)
class _Work:
    paper: RunPaper
    masked_text: str


@dataclass
class _Tally:
    """What this execution did, kept by the tasks that screen papers concurrently."""

    done: int
    total: int
    failed: int = 0


class ScreenStage:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        store: PaperStore,
        plans: PlanSource[UnitOfWork],
        inference: GuardedInference,
        masked: MaskedTextSource[UnitOfWork],
        rules: MaskingRuleService,
        instructions: ModelInstructionsService,
        audit: AuditLog,
        concurrency: int = DEFAULT_CONCURRENCY,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if concurrency < 1:
            raise ValueError("ScreenStage.concurrency must be at least 1")
        self._storage = storage
        self._store = store
        self._plans = plans
        self._inference = inference
        self._masked = masked
        self._rules = rules
        self._instructions = instructions
        self._audit = audit
        self._concurrency = concurrency
        self._clock = clock

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        plan = await accepted_plan_or_fail(self._storage, self._plans, context.review_id)
        if not plan.criteria:
            raise StageFailedError(NO_CRITERIA)
        async with self._storage.unit_of_work() as uow:
            run = await held_run(uow, context)
            paper_set = await adopted_or_fail(self._store, uow, run)
            await uow.commit()
            papers = await self._store.run_papers(uow, run)
            decided = {d.paper_id for d in await uow.screening.list_for_run(run.id)}
            work, skipped = await self._work(uow, run, papers, decided)
            privacy = await self._rules.privacy_for(uow, run.project_id)
            in_force = await self._instructions.in_force(uow, run.project_id, run.created_at)
        if not work and not decided and skipped["not_masked"]:
            raise StageFailedError(NOTHING_MASKED)
        _refuse_masked_criteria(plan, privacy.rules)
        sent = await self._instructions.as_sent(in_force, InstructionsKind.SCREENING, privacy)
        screening = _Screening(plan, sent)
        tally = _Tally(done=len(decided), total=len(decided) + len(work))
        inference = self._inference.for_project(privacy)
        await self._screen_all(context, run, screening, work, tally, inference)
        async with self._storage.unit_of_work() as uow:
            decisions = await uow.screening.list_for_run(run.id)
        tiers: dict[str, JsonValue] = {
            tier.value: sum(d.tier is tier for d in decisions) for tier in ScreeningTier
        }
        return {
            "papers": len(papers),
            "screened": len(decisions),
            "tiers": tiers,
            "unreadable": sum(d.is_unreadable for d in decisions),
            "skipped": dict[str, JsonValue](skipped),
            "failed": tally.failed,
            "manifest": {
                "is_masked": privacy.is_masked,
                "model_id": self._inference.model_id,
                "prompt_version": SCREEN_PROMPT_VERSION,
                "criteria_version": plan.version,
                "plan_version": plan.version,
                **({} if sent is None else {"instructions_version": sent.version}),
                **adoption_facts(paper_set),
            },
        }

    async def _work(
        self, uow: UnitOfWork, run: Run, papers: Sequence[RunPaper], decided: set[UUID]
    ) -> tuple[list[_Work], dict[str, int]]:
        kinds = {
            t.paper_id: t.kind
            for t in await uow.papers.list_texts([p.paper_id for p in papers])
        }
        work: list[_Work] = []
        skipped = {"no_abstract": 0, "not_masked": 0}
        for paper in papers:
            if paper.paper_id in decided:
                continue
            if kinds.get(paper.paper_id) is TextKind.TITLE:
                skipped["no_abstract"] += 1
                continue
            masked = await self._masked.masked_text(uow, run.project_id, paper.paper_id)
            if masked is None or not masked.strip():
                skipped["not_masked"] += 1
                continue
            work.append(_Work(paper, masked))
        return work, skipped

    async def _screen_all(
        self,
        context: StageContext,
        run: Run,
        screening: _Screening,
        work: Sequence[_Work],
        tally: _Tally,
        inference: GuardedInference,
    ) -> None:
        """Screen with at most `concurrency` calls in flight.

        After the first failure no new paper is started, but papers already in
        flight finish and are stored: their model call has been paid for.
        """
        slots = asyncio.BoundedSemaphore(self._concurrency)
        reporting = asyncio.Lock()
        stop = asyncio.Event()

        async def screen(item: _Work) -> None:
            async with slots:
                if stop.is_set():
                    return
                try:
                    await context.raise_if_cancelled()
                    await self._screen_one(
                        context, run, screening, item, tally, reporting, inference
                    )
                except BaseException:
                    stop.set()
                    raise

        results = await asyncio.gather(*(screen(item) for item in work), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _screen_one(
        self,
        context: StageContext,
        run: Run,
        screening: _Screening,
        item: _Work,
        tally: _Tally,
        reporting: asyncio.Lock,
        inference: GuardedInference,
    ) -> None:
        request = screening.request(item.masked_text)
        try:
            decision = await self._decide(run, screening.plan, item, request, inference)
        except PromptRefusedError as exc:
            _log.warning(
                "screening_prompt_refused",
                run_id=str(run.id),
                paper_id=str(item.paper.paper_id),
                reason=str(exc),
            )
            async with reporting:
                tally.failed += 1
                tally.done += 1
                await context.progress(
                    tally.done,
                    tally.total,
                    f"FAILED · {_PRIVACY_STOPPED}",
                    screened=screened_item(
                        item.paper.paper_id, decision=None, title=None, reason=_PRIVACY_STOPPED
                    ),
                )
            return
        async with self._storage.unit_of_work() as uow:
            stored = await self._store.seal_decision(uow, run.project_id, decision)
            if await uow.screening.add(stored):
                await self._audit.record(
                    uow,
                    SYSTEM,
                    "screening.decided",
                    "paper",
                    str(decision.paper_id),
                    scope_id=run.project_id,
                    details={"run_id": str(run.id)},
                )
                await uow.commit()
        title = one_line(item.masked_text, _MAX_TITLE)
        reason = one_line(decision.rationale, _MAX_REASON)
        async with reporting:
            tally.done += 1
            await context.progress(
                tally.done,
                tally.total,
                f"{_LABEL_CODES[decision.tier]} · {title} — {reason}",
                screened=screened_item(
                    decision.paper_id, decision=decision, title=title, reason=reason
                ),
            )

    async def _decide(
        self,
        run: Run,
        plan: ReviewPlan,
        item: _Work,
        request: ChatRequest,
        inference: GuardedInference,
    ) -> ScreeningDecision:
        paper = item.paper
        for attempt in (1, 2):
            try:
                result = await inference.chat(request)
            except InvalidStructuredOutputError as exc:
                _refused(run, paper, attempt, exc)
                continue
            except InferenceUnavailableError as exc:
                # The reason may name hosts and addresses: the operator's log only.
                _log.warning("screening_model_unavailable", run_id=str(run.id), reason=str(exc))
                limited = isinstance(exc, InferenceRateLimitedError)
                raise StageFailedError(MODEL_RATE_LIMITED if limited else MODEL_DOWN) from exc
            try:
                return self._decision(
                    run, plan, paper, result.output, result.model_id, item.masked_text
                )
            except ValueError as exc:
                _refused(run, paper, attempt, exc)
                continue
        return ScreeningDecision.unreadable(
            run_id=run.id,
            paper_id=paper.paper_id,
            model_id=self._inference.model_id,
            prompt_version=SCREEN_PROMPT_VERSION,
            criteria_version=plan.version,
            evidence_kind=TextKind.ABSTRACT,
            decided_at=self._clock(),
        )

    def _decision(
        self,
        run: Run,
        plan: ReviewPlan,
        paper: RunPaper,
        output: Mapping[str, Any],
        model_id: str,
        masked_text: str,
    ) -> ScreeningDecision:
        """The answer as a decision, or `ValueError` if it contradicts itself or its text."""
        label = ScreeningLabel(output["label"])
        matched = tuple(c.id for c in plan.criteria if c.id in output["criteria_matched"])
        answers = _criterion_answers(plan, output.get("answers"), masked_text)
        return ScreeningDecision(
            run_id=run.id,
            paper_id=paper.paper_id,
            label=label,
            score=float(output["score"]),
            priority=Priority(output["priority"]),
            rationale=str(output["rationale"]),
            criteria_matched=matched,
            exclusion_reasons=exclusion_reasons(
                label,
                matched,
                [c.id for c in plan.criteria if c.kind is CriterionKind.INCLUSION],
                [c.id for c in plan.criteria if c.kind is CriterionKind.EXCLUSION],
            ),
            model_id=model_id,
            prompt_version=SCREEN_PROMPT_VERSION,
            criteria_version=plan.version,
            evidence_kind=TextKind.ABSTRACT,
            decided_at=self._clock(),
            criterion_answers=answers,
        )


def _refuse_masked_criteria(plan: ReviewPlan, rules: Sequence[MaskingRule]) -> None:
    # A rule added after the plan was accepted. The guard would refuse every
    # paper; one plain failure naming the criterion says why.
    try:
        refuse_rule_terms(plan_text_fields((), plan.criteria), rules)
    except ValueError as exc:
        raise StageFailedError(f"{exc} Change the plan, then run again.") from exc


def _refused(run: Run, paper: RunPaper, attempt: int, error: Exception) -> None:
    # Ids and our own messages only: they name a schema path or a criterion id,
    # never the paper's text or the model's words.
    _log.warning(
        "screening_answer_refused",
        run_id=str(run.id),
        paper_id=str(paper.paper_id),
        attempt=attempt,
        reason=str(error),
    )


def _criterion_answers(
    plan: ReviewPlan, given: Any, masked_text: str
) -> tuple[CriterionAnswer, ...]:
    """The answers in the plan's order, each quote located in `masked_text`.

    `ValueError` unless every criterion has exactly one answer and every quote is found.
    """
    if not given:
        return ()
    by_id = {a["id"]: a for a in given}
    if len(by_id) != len(given) or set(by_id) != {c.id for c in plan.criteria}:
        raise ValueError("the answers must answer every criterion exactly once")
    return tuple(
        CriterionAnswer.located(
            c.id,
            CriterionVerdict(by_id[c.id]["answer"]),
            by_id[c.id]["quote"] or None,
            masked_text,
        )
        for c in plan.criteria
    )
