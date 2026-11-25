"""The loop that answers a follow-up question from a review's evidence.

It reuses the brief's `LoopRunner` and `revise_until_done`; only the prompts differ, and
the answer is up to six claims, or none when nothing retrieved answers it. The question is
the search; the Planner only decides whether the message asks about the evidence at all,
or asks to change the search, which is not answered here. Only masked text goes in, and
only the Writer gets the project's writing instructions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.application.brief_loop import (
    CLAIM_SCHEMA,
    CRITIC_SCHEMA,
    INVALID_MESSAGE,
    MAX_ROUNDS_LIMIT,
    CriticReview,
    LoopFailedError,
    LoopPrompt,
    LoopRunner,
    StepSink,
    array_schema,
    object_schema,
    parse_claim,
    parse_review,
    review_output,
    revise_until_done,
)
from app.application.inference import GuardedInference
from app.briefs.base import Retriever
from app.domain.briefs import (
    AgentRole,
    AgentStep,
    Problem,
    RetrievedPassage,
    SourceKind,
    StopReason,
    claim_to_dict,
)
from app.domain.model_instructions import SentInstructions
from app.domain.questions import MAX_ANSWER_CLAIMS, Answer, check_answer
from app.domain.runs import JsonValue
from app.domain.screening import Passage

PLANNER_PROMPT_VERSION = "answer-planner/1"
WRITER_PROMPT_VERSION = "answer-writer/1"
CRITIC_PROMPT_VERSION = "answer-critic/1"
# An answer is shorter than a brief: two rounds at most, so four model calls.
DEFAULT_MAX_ROUNDS = 2

ANSWER_SCHEMA = object_schema(claims=array_schema(CLAIM_SCHEMA, 0, MAX_ANSWER_CLAIMS))
INTENT_SCHEMA = object_schema(intent={"enum": ["evidence", "change_search"]})

_PLANNER_PROMPT = """\
You sort a message sent to the follow-up box of a humanitarian evidence review. Answer \
"evidence" if it asks about what the papers and documents already found say, or about \
the decision brief. Answer "change_search" if it asks to find more or different papers: \
to change the search terms or booleans, the sources, years, places or languages, the \
inclusion criteria, or to search again. When unsure, answer "evidence". Placeholders \
such as [PERSON_1] replace personal details. Answer with JSON only."""

_WRITER_PROMPT = """\
You answer a humanitarian team's follow-up question from the passages given, and from \
nothing else. Placeholders such as [PERSON_1] replace personal details; keep them as written.
Answer with up to 6 short claims. Each claim is either "evidence", citing the tags of the \
passages that say it, or "hypothesis", your own reasoning with no citations. Cite only tags \
from the passages given. Never say which option is best. Use hedged words where the \
passages are weak. "earlier" holds the thread's previous questions and answers, for \
context only. If no passage answers the question, answer with no claims.
If feedback is given, fix every point in it. Answer with JSON only."""

_CRITIC_PROMPT = """\
You review a draft answer to a follow-up question against the passages it was written \
from. Score each of coverage, groundedness, disagreements, uncertainty, \
humanitarian_awareness and specificity as 0 (poor), 1 (partly) or 2 (good). Problems \
found by the automatic checks are listed; a draft with problems must be revised. Answer \
"accept" if the answer is grounded in the passages and answers the question; otherwise \
"revise", with the issues to fix and up to 6 short search phrases for missing evidence. \
Answer with JSON only."""

_PLANNER = LoopPrompt(AgentRole.PLANNER, PLANNER_PROMPT_VERSION, _PLANNER_PROMPT, INTENT_SCHEMA)
_WRITER = LoopPrompt(AgentRole.WRITER, WRITER_PROMPT_VERSION, _WRITER_PROMPT, ANSWER_SCHEMA)
_CRITIC = LoopPrompt(AgentRole.CRITIC, CRITIC_PROMPT_VERSION, _CRITIC_PROMPT, CRITIC_SCHEMA)


@dataclass(frozen=True, slots=True)
class Turn:
    """An earlier question of the thread and its answer, both masked."""

    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """`stopped_because` is `None` when nothing was retrieved, so no model was asked."""

    answer: Answer
    model_id: str
    rounds: int
    kept_round: int
    stopped_because: StopReason | None
    warnings: tuple[Problem, ...]
    evidence: tuple[RetrievedPassage, ...]
    steps: tuple[AgentStep, ...]
    manifest: dict[str, JsonValue]
    instructions: SentInstructions | None = None


async def _ignore(step: AgentStep) -> None:
    return None


class AnswerLoop:
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

    async def run(
        self,
        question: str,
        earlier: Sequence[Turn],
        corpus: Sequence[Passage],
        on_step: StepSink = _ignore,
        instructions: SentInstructions | None = None,
    ) -> AnswerResult:
        """Answer the masked `question` from `corpus`, with the thread's `earlier` turns.

        `instructions` are the project's writing instructions as sent, for the Writer.
        Nothing retrieved is an answer, not an error: "nothing in this project
        answers that". Raises `LoopFailedError` when no usable draft came back.
        """
        runner = LoopRunner(
            self._inference, self._retriever, self._timer, tuple(corpus), on_step
        )
        manifest: dict[str, JsonValue] = {
            "prompt_versions": {
                "planner": PLANNER_PROMPT_VERSION,
                "writer": WRITER_PROMPT_VERSION,
                "critic": CRITIC_PROMPT_VERSION,
            },
            "retriever": self._retriever.retriever_id,
            "max_rounds": self._max_rounds,
        }
        if corpus:
            await runner.retrieve(0, [question, *(t.question for t in earlier[-1:])])
        if not runner.evidence:
            return AnswerResult(
                Answer(()),
                self._inference.model_id,
                0,
                0,
                None,
                (),
                (),
                tuple(runner.steps),
                manifest,
            )
        if await self._asks_to_change_search(runner, question):
            return AnswerResult(
                Answer((), asks_to_change_search=True),
                runner.model_id,
                0,
                0,
                None,
                (),
                (),
                tuple(runner.steps),
                manifest,
            )
        if instructions is not None:
            manifest["instructions_version"] = instructions.version
        writer = _WRITER.instructed(instructions)
        kinds = runner.source_kinds()
        turns: list[JsonValue] = [{"question": t.question, "answer": t.answer} for t in earlier]

        async def write(number: int, feedback: tuple[str, ...]) -> Answer | None:
            payload: dict[str, JsonValue] = {
                "question": question,
                "earlier": turns,
                "passages": runner.passages(),
                "feedback": list(feedback),
            }
            return await runner.ask(
                writer, number, payload, lambda a: _parse_answer(a, kinds), _answer_output
            )

        async def review(
            number: int, answer: Answer, problems: tuple[Problem, ...]
        ) -> CriticReview | None:
            payload: dict[str, JsonValue] = {
                "question": question,
                "passages": runner.passages(),
                "draft": [claim_to_dict(c) for c in answer.claims],
                "problems": [p.message for p in problems],
            }
            return await runner.ask(
                _CRITIC,
                number,
                payload,
                parse_review,
                lambda r: review_output(r, is_overruled=bool(problems)),
            )

        rounds = await revise_until_done(runner, self._max_rounds, write, check_answer, review)
        if rounds.kept is None:
            raise LoopFailedError(INVALID_MESSAGE)
        manifest["model_ids"] = {
            "writer": runner.model_id_of(AgentRole.WRITER),
            "critic": runner.model_id_of(AgentRole.CRITIC),
        }
        return AnswerResult(
            answer=rounds.kept.body,
            model_id=runner.model_id,
            rounds=rounds.rounds,
            kept_round=rounds.kept.round_number,
            stopped_because=rounds.stopped_because,
            warnings=rounds.kept.problems,
            evidence=tuple(runner.evidence.values()),
            steps=tuple(runner.steps),
            manifest=manifest,
            instructions=instructions,
        )

    @staticmethod
    async def _asks_to_change_search(runner: LoopRunner, question: str) -> bool:
        # Asked only once something was retrieved: with nothing found, the answer already
        # offers Change the search, and no model call is spent on it.
        intent = await runner.ask(
            _PLANNER,
            0,
            {"message": question},
            lambda output: str(output["intent"]),
            lambda value: {"intent": value},
        )
        # Unreadable twice: answered as a question, as before this step existed.
        return intent == "change_search"


def _parse_answer(answer: Mapping[str, Any], kinds: Mapping[str, SourceKind]) -> Answer:
    return Answer(tuple(parse_claim(c, kinds) for c in answer["claims"]))


def _answer_output(answer: Answer) -> dict[str, JsonValue]:
    return {
        "claims": len(answer.claims),
        "evidence_claims": sum(1 for c in answer.claims if c.is_evidence),
        "nothing_found": answer.nothing_found,
    }
