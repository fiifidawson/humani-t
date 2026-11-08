"""The agent loop that writes a decision brief, as a small state machine in plain Python.

Planner (round 0) → Retriever → then per round: Writer → checks → Critic, and
the Retriever again for what the Critic asked for. It stops when the Critic
accepts a draft the checks pass, at `max_rounds`, or when a role answers
invalidly twice in a row (dependencies.md §3: the CHITCHAT loop, not AMMORE's
framework). Planner, Writer and Critic are one `GuardedInference` call each,
with a versioned prompt and a JSON schema; the Retriever and the checks are code.

Only masked text goes in: the caller passes the masked question, and passages
are masked before they are stored. Every step is reported as it finishes and
kept as the brief's trace. Storage, encryption and the run belong to the stage
(`application/briefs.py`); this module holds no state between runs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.application.inference import GuardedInference
from app.briefs.base import Retriever
from app.domain.briefs import (
    CRITIC_DIMENSIONS,
    AgentRole,
    AgentStep,
    Assessment,
    BriefBody,
    Citation,
    Claim,
    ClaimSupport,
    Conflict,
    ConflictType,
    Consideration,
    CriticScores,
    EvidenceStrength,
    Gap,
    Option,
    Problem,
    RetrievedPassage,
    SourceKind,
    StepStatus,
    StopReason,
    TradeOff,
    body_to_dict,
    check_brief,
)
from app.domain.inference import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    ChatRole,
    InferenceError,
    InvalidStructuredOutputError,
    PromptRefusedError,
)
from app.domain.runs import JsonValue
from app.domain.screening import Passage

PLANNER_PROMPT_VERSION = "brief-planner/1"
WRITER_PROMPT_VERSION = "brief-writer/1"
CRITIC_PROMPT_VERSION = "brief-critic/1"

DEFAULT_MAX_ROUNDS = 3
MAX_ROUNDS_LIMIT = 10
# A local model's context is small: the Writer sees at most this many
# passages, gathered a few per search.
PASSAGES_PER_QUERY = 4
MAX_EVIDENCE = 40
MAX_QUERIES_PER_SEARCH = 6
# An invalid answer is asked for once more; two in a row stop the loop.
_ATTEMPTS = 2

NO_EVIDENCE_MESSAGE = (
    "No papers were included in this run, so there is nothing to write a brief from. "
    "Check the screening results, then run the brief again."
)
REFUSED_MESSAGE = (
    "The privacy check stopped a prompt before it was sent, so nothing was sent to the model."
)
UNAVAILABLE_MESSAGE = "The model server isn't answering. Try again in a minute."
INVALID_MESSAGE = (
    "The model's answers did not match the expected format twice in a row. Try again, or "
    "ask your administrator about the model."
)
_REFUSED_STEP = "The privacy check stopped this prompt; nothing was sent."
_UNAVAILABLE_STEP = "The model server isn't answering."
_INVALID_STEP = "The model's answer did not match the expected format."

TEXT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 600}
SHORT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 200}
_CITATION = {"type": "string", "minLength": 1, "maxLength": 32}


def _claim_schema(support: ClaimSupport, citations: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "support", "citations"],
        "properties": {
            "text": TEXT_SCHEMA,
            "support": {"enum": [support.value]},
            "citations": {"type": "array", "items": _CITATION, **citations},
        },
    }


# The honesty rule lives in the format, so a strict model cannot break it: evidence
# cites at least one passage, and a hypothesis, the model's own reasoning, cites
# none. Left to the domain check alone, gpt-5-mini broke it twice in a row.
EVIDENCE_CLAIM_SCHEMA = _claim_schema(ClaimSupport.EVIDENCE, {"minItems": 1, "maxItems": 8})
HYPOTHESIS_CLAIM_SCHEMA = _claim_schema(ClaimSupport.HYPOTHESIS, {"maxItems": 0})
CLAIM_SCHEMA: dict[str, Any] = {"anyOf": [EVIDENCE_CLAIM_SCHEMA, HYPOTHESIS_CLAIM_SCHEMA]}


def array_schema(items: Mapping[str, Any], low: int, high: int) -> dict[str, Any]:
    return {"type": "array", "minItems": low, "maxItems": high, "items": dict(items)}


def object_schema(**properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": {k: dict(v) for k, v in properties.items()},
    }


PLANNER_SCHEMA = object_schema(
    decision=SHORT_SCHEMA,
    options=array_schema(object_schema(name=SHORT_SCHEMA, description=TEXT_SCHEMA), 2, 4),
    criteria=array_schema(SHORT_SCHEMA, 2, 8),
    sub_questions=array_schema(SHORT_SCHEMA, 1, MAX_QUERIES_PER_SEARCH),
)

WRITER_SCHEMA = object_schema(
    bottom_line=array_schema(CLAIM_SCHEMA, 2, 3),
    options=array_schema(
        object_schema(
            name=SHORT_SCHEMA,
            description=TEXT_SCHEMA,
            strength={"enum": [s.value for s in EvidenceStrength]},
            benefits=array_schema(CLAIM_SCHEMA, 0, 6),
            risks=array_schema(CLAIM_SCHEMA, 0, 6),
        ),
        2,
        4,
    ),
    trade_offs=array_schema(
        object_schema(
            criterion=SHORT_SCHEMA,
            assessments=array_schema(
                object_schema(option=SHORT_SCHEMA, claim=CLAIM_SCHEMA), 2, 4
            ),
        ),
        1,
        10,
    ),
    conflicts=array_schema(
        object_schema(
            question=SHORT_SCHEMA,
            type={"enum": [t.value for t in ConflictType]},
            positions=array_schema(CLAIM_SCHEMA, 2, 4),
            # What a disagreement means is always the model's reasoning.
            meaning={"anyOf": [HYPOTHESIS_CLAIM_SCHEMA, {"type": "null"}]},
        ),
        0,
        5,
    ),
    considerations=array_schema(
        object_schema(criterion=SHORT_SCHEMA, option=SHORT_SCHEMA, because=CLAIM_SCHEMA), 0, 6
    ),
    gaps=array_schema(TEXT_SCHEMA, 0, 8),
)

_SCORE = {"type": "integer", "minimum": 0, "maximum": 2}
CRITIC_SCHEMA = object_schema(
    verdict={"enum": ["accept", "revise"]},
    scores=object_schema(
        coverage=_SCORE,
        groundedness=_SCORE,
        disagreements=_SCORE,
        uncertainty=_SCORE,
        humanitarian_awareness=_SCORE,
        specificity=_SCORE,
    ),
    issues=array_schema(SHORT_SCHEMA, 0, 8),
    queries=array_schema(SHORT_SCHEMA, 0, MAX_QUERIES_PER_SEARCH),
)

_PLANNER_PROMPT = """\
You plan a decision brief for a humanitarian team. You receive their question; \
personal details in it are already replaced by placeholders such as [LOCATION_1]. \
Keep placeholders exactly as written.
Answer with JSON only:
- decision: the decision the team faces, in one sentence;
- options: the 2 to 4 courses of action they are choosing between, each with a short \
description. Do not favour any of them;
- criteria: what the options should be compared on. Always include the humanitarian \
principles that matter here (humanity, impartiality, neutrality, independence, do no harm);
- sub_questions: short search phrases, in plain words, that would find evidence about \
each option against the criteria."""

_WRITER_PROMPT = """\
You write a decision brief for a humanitarian team from the passages given, and from \
nothing else. Placeholders such as [PERSON_1] replace personal details; keep them as written.
Rules:
- Every claim is either "evidence", citing the tags of the passages that say it, or \
"hypothesis", your own reasoning with no citations. Cite only tags from the passages given.
- Compare the options; never say which is best, never rank them, never total a score.
- Rate each option's evidence: strong, moderate, weak or insufficient. Use hedged words \
when the evidence is weak.
- trade_offs: one row per criterion, with one assessment for every option, and the \
relevant humanitarian principles among the criteria.
- conflicts: where passages disagree, typed hierarchy (stronger and weaker designs), \
context (different settings) or noise (small, old or unclear studies).
- considerations: "if this criterion matters most, this option, because ...".
- gaps: what the passages do not cover.
- bottom_line: 2 or 3 sentences.
If feedback is given, fix every point in it. Answer with JSON only."""

_CRITIC_PROMPT = """\
You review a draft decision brief against the passages it was written from. Score each \
of coverage, groundedness, disagreements, uncertainty, humanitarian_awareness and \
specificity as 0 (poor), 1 (partly) or 2 (good). Problems found by the automatic checks \
are listed; a draft with problems must be revised. Answer "accept" only if the brief is \
fit to show a decision-maker; otherwise "revise", with the issues to fix and up to 6 \
short search phrases for evidence that is missing. Answer with JSON only."""


class LoopFailedError(Exception):
    """The loop could not produce anything to keep. The message is safe to show members."""


@dataclass(frozen=True, slots=True)
class LoopPrompt:
    role: AgentRole
    version: str
    system: str
    schema: Mapping[str, Any]


_PLANNER = LoopPrompt(
    AgentRole.PLANNER, PLANNER_PROMPT_VERSION, _PLANNER_PROMPT, PLANNER_SCHEMA
)
_WRITER = LoopPrompt(AgentRole.WRITER, WRITER_PROMPT_VERSION, _WRITER_PROMPT, WRITER_SCHEMA)
_CRITIC = LoopPrompt(AgentRole.CRITIC, CRITIC_PROMPT_VERSION, _CRITIC_PROMPT, CRITIC_SCHEMA)


@dataclass(frozen=True, slots=True)
class _Plan:
    decision: str
    options: tuple[tuple[str, str], ...]
    criteria: tuple[str, ...]
    sub_questions: tuple[str, ...]

    def to_prompt(self) -> dict[str, JsonValue]:
        return {
            "decision": self.decision,
            "options": [{"name": n, "description": d} for n, d in self.options],
            "criteria": list(self.criteria),
        }


@dataclass(frozen=True, slots=True)
class CriticReview:
    is_accepted: bool
    scores: CriticScores
    issues: tuple[str, ...]
    queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Draft[T]:
    """One round's draft, what the checks found in it and what the Critic scored it."""

    round_number: int
    body: T
    problems: tuple[Problem, ...]
    scores: CriticScores | None

    def rank_key(self) -> tuple[bool, int, int]:
        # Internal only, to choose which draft to keep; the sum is never stored or shown.
        points = -1
        if self.scores is not None:
            points = sum(int(getattr(self.scores, name)) for name in CRITIC_DIMENSIONS)
        return (not self.problems, points, self.round_number)


@dataclass(frozen=True, slots=True)
class Rounds[T]:
    """How the rounds ended: the draft kept (`None` if none was usable) and why."""

    kept: Draft[T] | None
    rounds: int
    stopped_because: StopReason


async def revise_until_done[T](
    runner: LoopRunner,
    max_rounds: int,
    write: Callable[[int, tuple[str, ...]], Awaitable[T | None]],
    checks: Callable[[T, frozenset[str]], tuple[Problem, ...]],
    review: Callable[[int, T, tuple[Problem, ...]], Awaitable[CriticReview | None]],
) -> Rounds[T]:
    """Writer, checks and Critic each round, retrieving what the Critic asks for, until done.

    Done is: the Critic accepts a draft the checks pass; `max_rounds`; a role
    answering invalidly twice in a row; or a round that did no better than the
    kept draft with no new evidence to do better with. The kept draft is the
    accepted one, else the best by `Draft.rank_key`.
    """
    kept: Draft[T] | None = None
    feedback: tuple[str, ...] = ()
    rounds = 0
    for round_number in range(1, max_rounds + 1):
        rounds = round_number
        body = await write(round_number, feedback)
        if body is None:
            return Rounds(kept, rounds, StopReason.INVALID_OUTPUT)
        problems = await runner.check(round_number, partial(checks, body))
        verdict = await review(round_number, body, problems)
        draft = Draft(round_number, body, problems, None if verdict is None else verdict.scores)
        improved = kept is None or draft.rank_key() > kept.rank_key()
        kept = draft if kept is None or draft.rank_key() >= kept.rank_key() else kept
        if verdict is None:
            return Rounds(kept, rounds, StopReason.INVALID_OUTPUT)
        if verdict.is_accepted and not problems:
            return Rounds(draft, rounds, StopReason.ACCEPTED)
        if round_number == max_rounds:
            break
        feedback = (*verdict.issues, *(p.message for p in problems))
        new = await runner.retrieve(round_number, verdict.queries) if verdict.queries else 0
        # Another round could only rewrite the same evidence, and this one did no better.
        if not improved and new == 0:
            return Rounds(kept, rounds, StopReason.NO_PROGRESS)
    return Rounds(kept, rounds, StopReason.MAX_ROUNDS)


@dataclass(frozen=True, slots=True)
class LoopResult:
    body: BriefBody
    model_id: str
    rounds: int
    kept_round: int
    stopped_because: StopReason
    warnings: tuple[Problem, ...]
    quality: CriticScores | None
    evidence: tuple[RetrievedPassage, ...]
    steps: tuple[AgentStep, ...]
    manifest: dict[str, JsonValue]


type StepSink = Callable[[AgentStep], Awaitable[None]]


class BriefLoop:
    def __init__(
        self,
        inference: GuardedInference,
        retriever: Retriever,
        *,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_rounds <= MAX_ROUNDS_LIMIT:
            raise ValueError(f"max_rounds must be between 1 and {MAX_ROUNDS_LIMIT}")
        self._inference = inference
        self._retriever = retriever
        self._max_rounds = max_rounds
        self._timer = timer

    @property
    def max_rounds(self) -> int:
        return self._max_rounds

    async def run(
        self, question: str, corpus: Sequence[Passage], on_step: StepSink
    ) -> LoopResult:
        """Write a brief for the masked `question` from `corpus`, reporting each step.

        Raises `LoopFailedError` when no draft could be kept at all. Anything
        `on_step` raises (a cancelled run) ends the loop where it is.
        """
        if not corpus:
            raise LoopFailedError(NO_EVIDENCE_MESSAGE)
        runner = LoopRunner(
            self._inference, self._retriever, self._timer, tuple(corpus), on_step
        )
        session = _Session(runner, self._max_rounds, question)
        return await session.run()


@dataclass
class LoopRunner:
    """The machinery every agent loop shares: model steps, retrieval, checks, the trace.

    One per run of a loop, discarded when it ends. The brief loop below and the
    follow-up answer loop (`application/questions.py`) differ only in their
    prompts and in what they do between steps.
    """

    inference: GuardedInference
    retriever: Retriever
    timer: Callable[[], float]
    corpus: tuple[Passage, ...]
    on_step: StepSink
    steps: list[AgentStep] = field(default_factory=list[AgentStep])
    evidence: dict[tuple[str, str, str], RetrievedPassage] = field(
        default_factory=dict[tuple[str, str, str], RetrievedPassage]
    )
    model_ids: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def model_id(self) -> str:
        return self.model_id_of(AgentRole.WRITER)

    def model_id_of(self, role: AgentRole) -> str:
        return self.model_ids.get(role.value, self.inference.model_id)

    def source_kinds(self) -> dict[str, SourceKind]:
        return {p.tag: p.source_kind for p in self.corpus}

    def passages(self) -> list[JsonValue]:
        return [{"tag": r.passage.tag, "text": r.passage.text} for r in self.evidence.values()]

    def retrieved_tags(self) -> frozenset[str]:
        return frozenset(r.passage.tag for r in self.evidence.values())

    async def emit(self, step: AgentStep) -> None:
        self.steps.append(step)
        await self.on_step(step)

    def _seconds(self, started: float) -> float:
        return round(self.timer() - started, 3)

    async def retrieve(self, round_number: int, queries: Sequence[str]) -> int:
        """Search each query, keep what is new and report one step; return how many are new."""
        started = self.timer()
        wanted = tuple(dict.fromkeys(q.strip() for q in queries if q.strip()))
        wanted = wanted[:MAX_QUERIES_PER_SEARCH]
        found = new = 0
        for query in wanted:
            hits = await self.retriever.search(self.corpus, query, limit=PASSAGES_PER_QUERY)
            found += len(hits)
            for passage in hits:
                key = (passage.tag, passage.text, passage.locator)
                if key not in self.evidence and len(self.evidence) < MAX_EVIDENCE:
                    self.evidence[key] = RetrievedPassage(passage, query, round_number)
                    new += 1
        await self.emit(
            AgentStep(
                round_number,
                AgentRole.RETRIEVER,
                StepStatus.OK,
                seconds=self._seconds(started),
                queries=wanted,
                passages_found=found,
                passages_new=new,
                passages_total=len(self.evidence),
            )
        )
        return new

    async def check(
        self, round_number: int, run_checks: Callable[[frozenset[str]], tuple[Problem, ...]]
    ) -> tuple[Problem, ...]:
        """Run code checks against what was retrieved, and report them as one step."""
        started = self.timer()
        problems = run_checks(self.retrieved_tags())
        await self.emit(
            AgentStep(
                round_number,
                AgentRole.CHECKS,
                StepStatus.OK,
                seconds=self._seconds(started),
                problems=problems,
            )
        )
        return problems

    async def ask[T](
        self,
        prompt: LoopPrompt,
        round_number: int,
        payload: Mapping[str, JsonValue],
        parse: Callable[[Mapping[str, Any]], T],
        describe: Callable[[T], dict[str, JsonValue]],
    ) -> T | None:
        """One role's answer, asked for again once if invalid; `None` after two invalid ones.

        Raises `LoopFailedError` when the prompt is refused or the model server
        does not answer: retrying either would only fail again.
        """
        problem: str | None = None
        for _ in range(_ATTEMPTS):
            retry = {} if problem is None else {"previous_answer_problem": problem}
            answer = await self._call(
                prompt, round_number, {**payload, **retry}, parse, describe
            )
            if not isinstance(answer, _Invalid):
                return answer
            problem = answer.reason
        return None

    async def _call[T](
        self,
        prompt: LoopPrompt,
        round_number: int,
        payload: Mapping[str, JsonValue],
        parse: Callable[[Mapping[str, Any]], T],
        describe: Callable[[T], dict[str, JsonValue]],
    ) -> T | _Invalid:
        request = ChatRequest(
            messages=(
                ChatMessage(ChatRole.SYSTEM, prompt.system),
                ChatMessage(ChatRole.USER, json.dumps(payload, ensure_ascii=False)),
            ),
            output_schema=prompt.schema,
        )

        def step(status: StepStatus, seconds: float, **extra: Any) -> AgentStep:
            return AgentStep(
                round_number,
                prompt.role,
                status,
                seconds=seconds,
                prompt_version=prompt.version,
                **extra,
            )

        started = self.timer()
        try:
            result = await self.inference.chat(request)
        except PromptRefusedError as exc:
            await self.emit(
                step(StepStatus.REFUSED, self._seconds(started), error=_REFUSED_STEP)
            )
            raise LoopFailedError(REFUSED_MESSAGE) from exc
        except InvalidStructuredOutputError as exc:
            await self.emit(
                step(StepStatus.INVALID, self._seconds(started), error=_INVALID_STEP)
            )
            # The path and rule only, never a value: see `inference/schema.py`.
            return _Invalid(str(exc))
        except InferenceError as exc:
            await self.emit(
                step(StepStatus.ERROR, self._seconds(started), error=_UNAVAILABLE_STEP)
            )
            raise LoopFailedError(UNAVAILABLE_MESSAGE) from exc
        seconds = self._seconds(started)
        usage = _usage(result)
        try:
            parsed = parse(result.output)
        except ValueError as exc:
            # Domain messages name the rule and the field, never the model's text.
            reason = f"The answer broke a rule: {exc}"
            await self.emit(step(StepStatus.INVALID, seconds, error=reason, **usage))
            return _Invalid(reason)
        self.model_ids[prompt.role.value] = result.model_id
        await self.emit(step(StepStatus.OK, seconds, output=describe(parsed), **usage))
        return parsed


@dataclass
class _Session:
    """One run of the brief loop: the plan, the drafts and when to stop."""

    runner: LoopRunner
    max_rounds: int
    question: str

    async def run(self) -> LoopResult:
        runner = self.runner
        plan = await runner.ask(
            _PLANNER, 0, {"question": self.question}, _parse_plan, _plan_output
        )
        if plan is None:
            raise LoopFailedError(INVALID_MESSAGE)
        await runner.retrieve(0, plan.sub_questions)
        rounds = await revise_until_done(
            runner,
            self.max_rounds,
            lambda number, feedback: self._write(number, plan, feedback),
            check_brief,
            lambda number, body, problems: self._review(number, plan, body, problems),
        )
        if rounds.kept is None:
            raise LoopFailedError(INVALID_MESSAGE)
        return self._result(rounds.kept, rounds.rounds, rounds.stopped_because)

    def _result(self, kept: Draft[BriefBody], rounds: int, stopped: StopReason) -> LoopResult:
        runner = self.runner
        roles = (AgentRole.PLANNER, AgentRole.WRITER, AgentRole.CRITIC)
        model_ids: dict[str, JsonValue] = {r.value: runner.model_id_of(r) for r in roles}
        return LoopResult(
            body=kept.body,
            model_id=runner.model_id,
            rounds=rounds,
            kept_round=kept.round_number,
            stopped_because=stopped,
            warnings=kept.problems,
            quality=kept.scores,
            evidence=tuple(runner.evidence.values()),
            steps=tuple(runner.steps),
            manifest={
                "model_ids": model_ids,
                "prompt_versions": {
                    "planner": PLANNER_PROMPT_VERSION,
                    "writer": WRITER_PROMPT_VERSION,
                    "critic": CRITIC_PROMPT_VERSION,
                },
                "retriever": runner.retriever.retriever_id,
                "max_rounds": self.max_rounds,
            },
        )

    async def _write(
        self, round_number: int, plan: _Plan, feedback: Sequence[str]
    ) -> BriefBody | None:
        kinds = self.runner.source_kinds()
        payload: dict[str, JsonValue] = {
            "question": self.question,
            **plan.to_prompt(),
            "passages": self.runner.passages(),
            "feedback": list(feedback),
        }
        return await self.runner.ask(
            _WRITER,
            round_number,
            payload,
            lambda answer: _parse_body(answer, kinds),
            _body_output,
        )

    async def _review(
        self, round_number: int, plan: _Plan, body: BriefBody, problems: Sequence[Problem]
    ) -> CriticReview | None:
        payload: dict[str, JsonValue] = {
            "question": self.question,
            **plan.to_prompt(),
            "passages": self.runner.passages(),
            "draft": body_to_dict(body),
            "problems": [p.message for p in problems],
        }
        return await self.runner.ask(
            _CRITIC,
            round_number,
            payload,
            parse_review,
            lambda review: review_output(review, is_overruled=bool(problems)),
        )


@dataclass(frozen=True, slots=True)
class _Invalid:
    reason: str


def _usage(result: ChatResult) -> dict[str, Any]:
    return {
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "model_id": result.model_id,
    }


def _parse_plan(answer: Mapping[str, Any]) -> _Plan:
    return _Plan(
        decision=str(answer["decision"]),
        options=tuple((str(o["name"]), str(o["description"])) for o in answer["options"]),
        criteria=tuple(str(c) for c in answer["criteria"]),
        sub_questions=tuple(str(q) for q in answer["sub_questions"]),
    )


def _plan_output(plan: _Plan) -> dict[str, JsonValue]:
    return {
        "decision": plan.decision,
        "options": [name for name, _ in plan.options],
        "criteria": list(plan.criteria),
        "sub_questions": list(plan.sub_questions),
    }


def parse_claim(answer: Mapping[str, Any], kinds: Mapping[str, SourceKind]) -> Claim:
    tags = [str(t).strip() for t in answer["citations"]]
    return Claim(
        str(answer["text"]),
        ClaimSupport(answer["support"]),
        tuple(Citation(t, kinds.get(t, _kind_from_tag(t))) for t in dict.fromkeys(tags)),
    )


def _kind_from_tag(tag: str) -> SourceKind:
    # Only for a tag nothing was retrieved under, which the checks report anyway:
    # team documents are cited as D…, papers as P… (UI_UX_SPEC §8.4).
    return SourceKind.DOCUMENT if tag.upper().startswith("D") else SourceKind.PAPER


def _parse_body(answer: Mapping[str, Any], kinds: Mapping[str, SourceKind]) -> BriefBody:
    def claim(value: Mapping[str, Any]) -> Claim:
        return parse_claim(value, kinds)

    return BriefBody(
        bottom_line=tuple(claim(c) for c in answer["bottom_line"]),
        options=tuple(
            Option(
                name=str(o["name"]),
                description=str(o["description"]),
                strength=EvidenceStrength(o["strength"]),
                benefits=tuple(claim(c) for c in o["benefits"]),
                risks=tuple(claim(c) for c in o["risks"]),
            )
            for o in answer["options"]
        ),
        trade_offs=tuple(
            TradeOff(
                str(row["criterion"]),
                tuple(
                    Assessment(str(a["option"]), claim(a["claim"])) for a in row["assessments"]
                ),
            )
            for row in answer["trade_offs"]
        ),
        conflicts=tuple(
            Conflict(
                str(c["question"]),
                ConflictType(c["type"]),
                tuple(claim(p) for p in c["positions"]),
                None if c["meaning"] is None else claim(c["meaning"]),
            )
            for c in answer["conflicts"]
        ),
        considerations=tuple(
            Consideration(str(c["criterion"]), str(c["option"]), claim(c["because"]))
            for c in answer["considerations"]
        ),
        gaps=tuple(Gap(str(g)) for g in answer["gaps"]),
    )


def _body_output(body: BriefBody) -> dict[str, JsonValue]:
    claims = [claim for _, claim in body.claims()]
    return {
        "options": [o.name for o in body.options],
        "claims": len(claims),
        "evidence_claims": sum(1 for c in claims if c.is_evidence),
        "cited_tags": len(body.cited_tags()),
    }


def parse_review(answer: Mapping[str, Any]) -> CriticReview:
    return CriticReview(
        is_accepted=answer["verdict"] == "accept",
        scores=CriticScores.from_payload(answer["scores"]),
        issues=tuple(str(i) for i in answer["issues"]),
        queries=tuple(str(q) for q in answer["queries"]),
    )


def review_output(review: CriticReview, *, is_overruled: bool) -> dict[str, JsonValue]:
    return {
        "verdict": "accept" if review.is_accepted else "revise",
        "overruled_by_checks": review.is_accepted and is_overruled,
        "scores": review.scores.to_payload(),
        "issues": list(review.issues),
        "queries": list(review.queries),
    }
