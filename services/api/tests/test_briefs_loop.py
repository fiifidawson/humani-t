"""The agent loop that writes a decision brief: Planner, Retriever, Writer, checks, Critic.

Sources: architecture §4.2 and §4.4, dependencies.md §3 (the CHITCHAT loop in
plain Python: the Critic reviews the Writer's draft, every agent returns a
schema, code checks the citations), UI_UX_SPEC §6.11.

The model is a scripted `FakeInferenceProvider` behind the real
`GuardedInference`; retrieval is the real BM25 retriever over a small corpus.
"""

from __future__ import annotations

import pytest

from app.application.brief_loop import (
    CRITIC_PROMPT_VERSION,
    PLANNER_PROMPT_VERSION,
    WRITER_PROMPT_VERSION,
    BriefLoop,
    LoopFailedError,
    LoopResult,
)
from app.application.inference import GuardedInference
from app.briefs.bm25 import Bm25Retriever
from app.domain.briefs import AgentStep, CriticScores, StopReason
from app.domain.inference import ChatRequest, ChatResult, InferenceUnavailableError
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from tests.brief_support import (
    CORPUS,
    INVALID,
    SCORES,
    SUB_QUESTIONS,
    RoleScript,
    accepted_first_time,
    critic_answer,
    planner_answer,
    role_of,
    text_sent,
    writer_answer,
)

QUESTION = "Should we switch from in-kind food to cash in the camps near [LOCATION_1]?"


class Clock:
    """A timer that moves two seconds every time it is read."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 2.0
        return self.now


class Harness:
    def __init__(self, script: RoleScript, *, max_rounds: int = 3, guard=None) -> None:
        self.script = script
        self.provider = FakeInferenceProvider(script, model_id="qwen3.5:9b")
        self.guard = guard or FakePromptGuard()
        self.loop = BriefLoop(
            GuardedInference(self.provider, self.guard),
            Bm25Retriever(),
            max_rounds=max_rounds,
            timer=Clock(),
        )
        self.reported: list[AgentStep] = []

    async def run(self) -> LoopResult:
        async def report(step: AgentStep) -> None:
            self.reported.append(step)

        return await self.loop.run(QUESTION, CORPUS, report)

    def requests_for(self, role: str) -> list[ChatRequest]:
        return [r for r in self.provider.requests if role_of(r) == role]


def trail(steps: tuple[AgentStep, ...] | list[AgentStep]) -> list[tuple[int, str, str]]:
    return [(s.round_number, s.role.value, s.status.value) for s in steps]


class TestAcceptedFirstTime:
    async def test_an_accepted_first_draft_stops_the_loop_in_round_one(self):
        h = Harness(accepted_first_time())

        result = await h.run()

        assert trail(result.steps) == [
            (0, "planner", "ok"),
            (0, "retriever", "ok"),
            (1, "writer", "ok"),
            (1, "checks", "ok"),
            (1, "critic", "ok"),
        ]
        assert (result.stopped_because, result.rounds, result.kept_round) == (
            StopReason.ACCEPTED,
            1,
            1,
        )

    async def test_every_step_is_reported_as_it_happens_and_kept_as_the_trace(self):
        h = Harness(accepted_first_time())

        result = await h.run()

        assert tuple(h.reported) == result.steps

    async def test_the_kept_draft_becomes_the_brief_with_the_critics_scores(self):
        result = await Harness(accepted_first_time()).run()

        assert [o.name for o in result.body.options] == ["Cash transfers", "In-kind food"]
        assert result.quality == CriticScores(**SCORES)
        assert result.warnings == ()

    async def test_the_retriever_searches_the_planners_sub_questions(self):
        result = await Harness(accepted_first_time()).run()

        retriever = result.steps[1]
        assert retriever.queries == SUB_QUESTIONS
        assert retriever.passages_total == len(result.evidence)
        assert {p.found_by for p in result.evidence} <= set(SUB_QUESTIONS)

    async def test_the_writer_sees_only_the_passages_that_were_retrieved(self):
        h = Harness(accepted_first_time())

        result = await h.run()

        sent = text_sent(h.requests_for("writer")[0])
        assert all(p.passage.text in sent for p in result.evidence)
        assert "Earthquake shelter" not in sent

    async def test_every_model_call_asks_for_a_schema_at_temperature_zero(self):
        h = Harness(accepted_first_time())

        await h.run()

        assert [role_of(r) for r in h.provider.requests] == ["planner", "writer", "critic"]
        assert all(r.temperature == 0.0 for r in h.provider.requests)

    async def test_each_model_step_records_its_seconds_tokens_and_prompt_version(self):
        result = await Harness(accepted_first_time()).run()

        by_role = {s.role.value: s for s in result.steps}
        assert by_role["planner"].prompt_version == PLANNER_PROMPT_VERSION
        assert by_role["writer"].prompt_version == WRITER_PROMPT_VERSION
        assert by_role["critic"].prompt_version == CRITIC_PROMPT_VERSION
        assert all(by_role[r].seconds == 2.0 for r in ("planner", "writer", "critic"))
        assert all((by_role[r].prompt_tokens or 0) > 0 for r in ("planner", "writer", "critic"))

    async def test_the_planner_step_carries_the_plan(self):
        result = await Harness(accepted_first_time()).run()

        output = result.steps[0].output or {}
        assert output["options"] == ["Cash transfers", "In-kind food"]
        assert output["sub_questions"] == list(SUB_QUESTIONS)

    async def test_the_manifest_names_the_model_and_prompt_version_of_each_role(self):
        result = await Harness(accepted_first_time()).run()

        assert result.manifest == {
            "model_ids": {
                "planner": "qwen3.5:9b",
                "writer": "qwen3.5:9b",
                "critic": "qwen3.5:9b",
            },
            "prompt_versions": {
                "planner": PLANNER_PROMPT_VERSION,
                "writer": WRITER_PROMPT_VERSION,
                "critic": CRITIC_PROMPT_VERSION,
            },
            "retriever": Bm25Retriever().retriever_id,
            "max_rounds": 3,
        }
        assert result.model_id == "qwen3.5:9b"


class TestChecksCatchInventedCitations:
    async def test_a_draft_citing_a_passage_never_retrieved_is_caught_and_revised(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005"), writer_answer()],
            critic=[critic_answer("accept"), critic_answer("accept")],
        )
        h = Harness(script)

        result = await h.run()

        checks = [s for s in result.steps if s.role.value == "checks"]
        assert [p.code for p in checks[0].problems] == ["citation_not_retrieved"]
        assert "P0005" in checks[0].problems[0].message
        assert checks[1].problems == ()
        assert (result.stopped_because, result.kept_round) == (StopReason.ACCEPTED, 2)

    async def test_the_critic_is_told_about_the_problems_and_cannot_accept_past_them(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005"), writer_answer()],
            critic=[critic_answer("accept"), critic_answer("accept")],
        )
        h = Harness(script)

        result = await h.run()

        assert "P0005" in text_sent(h.requests_for("critic")[0])
        critic = next(s for s in result.steps if s.role.value == "critic")
        assert (critic.output or {})["verdict"] == "accept"
        assert (critic.output or {})["overruled_by_checks"] is True

    async def test_the_writers_next_draft_is_asked_to_fix_the_problem(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005"), writer_answer()],
            critic=[critic_answer("accept"), critic_answer("accept")],
        )
        h = Harness(script)

        await h.run()

        assert "P0005" not in text_sent(h.requests_for("writer")[0])
        assert "P0005" in text_sent(h.requests_for("writer")[1])

    async def test_problems_still_unresolved_at_the_round_limit_are_kept_as_warnings(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(cite="P0005")],
            critic=[critic_answer("accept")],
        )

        result = await Harness(script, max_rounds=1).run()

        assert result.stopped_because is StopReason.MAX_ROUNDS
        assert [w.code for w in result.warnings] == ["citation_not_retrieved"]


class TestCriticRevisions:
    async def test_a_critic_rejecting_then_accepting_takes_two_rounds(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer(cite="P0005")],
            critic=[
                critic_answer("revise", queries=["earthquake shelter"], issues=["Shelter?"]),
                critic_answer("accept"),
            ],
        )

        result = await Harness(script).run()

        assert trail(result.steps) == [
            (0, "planner", "ok"),
            (0, "retriever", "ok"),
            (1, "writer", "ok"),
            (1, "checks", "ok"),
            (1, "critic", "ok"),
            (1, "retriever", "ok"),
            (2, "writer", "ok"),
            (2, "checks", "ok"),
            (2, "critic", "ok"),
        ]
        assert (result.stopped_because, result.rounds, result.kept_round) == (
            StopReason.ACCEPTED,
            2,
            2,
        )

    async def test_the_retriever_searches_what_the_critic_asked_for(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer(cite="P0005")],
            critic=[critic_answer("revise", queries=["earthquake shelter"]), critic_answer()],
        )

        result = await Harness(script).run()

        follow_up = result.steps[5]
        assert follow_up.queries == ("earthquake shelter",)
        assert follow_up.passages_new == 1
        assert "P0005" in {p.passage.tag for p in result.evidence}

    async def test_the_critics_issues_reach_the_next_draft(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer()],
            critic=[critic_answer("revise", issues=["Say more about cost."]), critic_answer()],
        )
        h = Harness(script)

        await h.run()

        assert "Say more about cost." in text_sent(h.requests_for("writer")[1])

    async def test_reaching_max_rounds_stops_the_loop(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer()],
            critic=[critic_answer("revise"), critic_answer("revise")],
        )

        result = await Harness(script, max_rounds=2).run()

        assert (result.stopped_because, result.rounds) == (StopReason.MAX_ROUNDS, 2)
        assert [s.role.value for s in result.steps].count("writer") == 2

    async def test_without_an_accepted_draft_the_best_scored_round_is_kept(self):
        weaker = {**SCORES, "coverage": 0, "groundedness": 0}
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer()],
            critic=[critic_answer("revise"), critic_answer("revise", scores=weaker)],
        )

        result = await Harness(script, max_rounds=2).run()

        assert result.kept_round == 1
        assert result.quality == CriticScores(**SCORES)

    async def test_a_draft_without_problems_is_kept_over_a_better_scored_one_with_problems(
        self,
    ):
        weaker = {**SCORES, "coverage": 0}
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer(cite="P0005")],
            critic=[critic_answer("revise", scores=weaker), critic_answer("accept")],
        )

        result = await Harness(script, max_rounds=2).run()

        assert (result.kept_round, result.warnings) == (1, ())


class TestNoProgress:
    async def test_no_new_evidence_and_no_better_score_stops_the_loop(self):
        weaker = {**SCORES, "coverage": 0}
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer()],
            critic=[critic_answer("revise"), critic_answer("revise", scores=weaker)],
        )

        result = await Harness(script, max_rounds=3).run()

        assert (result.stopped_because, result.rounds, result.kept_round) == (
            StopReason.NO_PROGRESS,
            2,
            1,
        )
        assert [s.role.value for s in result.steps].count("writer") == 2

    async def test_new_evidence_earns_another_round_even_without_a_better_score(self):
        weaker = {**SCORES, "coverage": 0}
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer(), writer_answer()],
            critic=[
                critic_answer("revise"),
                critic_answer("revise", scores=weaker, queries=["earthquake shelter"]),
                critic_answer("accept"),
            ],
        )

        result = await Harness(script, max_rounds=3).run()

        assert (result.stopped_because, result.rounds) == (StopReason.ACCEPTED, 3)

    async def test_a_better_score_earns_another_round_without_new_evidence(self):
        weaker = {**SCORES, "coverage": 0}
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), writer_answer(), writer_answer()],
            critic=[
                critic_answer("revise", scores=weaker),
                critic_answer("revise"),
                critic_answer("accept"),
            ],
        )

        result = await Harness(script, max_rounds=3).run()

        assert (result.stopped_because, result.rounds) == (StopReason.ACCEPTED, 3)


class TestInvalidOutput:
    async def test_an_invalid_answer_is_asked_for_once_more(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[INVALID, writer_answer()],
            critic=[critic_answer()],
        )

        result = await Harness(script).run()

        assert trail(result.steps)[2:4] == [(1, "writer", "invalid"), (1, "writer", "ok")]
        assert result.steps[2].error
        assert result.stopped_because is StopReason.ACCEPTED

    async def test_an_answer_that_breaks_a_brief_rule_counts_as_invalid(self):
        unknown_option = writer_answer()
        unknown_option["trade_offs"][0]["assessments"][1]["option"] = "Vouchers"
        script = RoleScript(
            planner=[planner_answer()],
            writer=[unknown_option, writer_answer()],
            critic=[critic_answer()],
        )

        result = await Harness(script).run()

        assert trail(result.steps)[2] == (1, "writer", "invalid")
        assert "TradeOff.assessments" in (result.steps[2].error or "")

    async def test_repeated_invalid_output_stops_the_loop_and_keeps_the_last_good_draft(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer(), INVALID, INVALID],
            critic=[critic_answer("revise")],
        )

        result = await Harness(script).run()

        assert (result.stopped_because, result.kept_round, result.rounds) == (
            StopReason.INVALID_OUTPUT,
            1,
            2,
        )

    async def test_a_critic_that_answers_invalidly_twice_stops_the_loop(self):
        script = RoleScript(
            planner=[planner_answer()],
            writer=[writer_answer()],
            critic=[INVALID, INVALID],
        )

        result = await Harness(script).run()

        assert result.stopped_because is StopReason.INVALID_OUTPUT
        assert result.quality is None

    async def test_repeated_invalid_output_before_any_draft_fails_in_plain_words(self):
        script = RoleScript(planner=[planner_answer()], writer=[INVALID, INVALID])
        h = Harness(script)

        with pytest.raises(LoopFailedError, match="format"):
            await h.run()

        assert [s.status.value for s in h.reported][-2:] == ["invalid", "invalid"]

    async def test_a_planner_that_answers_invalidly_twice_fails_the_brief(self):
        with pytest.raises(LoopFailedError):
            await Harness(RoleScript(planner=[INVALID, INVALID])).run()


class _Unreachable:
    model_id = "qwen3.5:9b"

    async def chat(self, request: ChatRequest) -> ChatResult:
        raise InferenceUnavailableError("connection refused to 10.0.0.7:11434")


class TestRefusalsAndOutages:
    async def test_a_refused_prompt_sends_nothing_and_fails_the_brief(self):
        h = Harness(accepted_first_time(), guard=FakePromptGuard("2 unmasked names"))

        with pytest.raises(LoopFailedError, match="privacy check"):
            await h.run()

        assert h.provider.requests == []
        assert trail(h.reported) == [(0, "planner", "refused")]

    async def test_an_unreachable_model_server_fails_in_plain_words(self):
        reported: list[AgentStep] = []

        async def report(step: AgentStep) -> None:
            reported.append(step)

        loop = BriefLoop(GuardedInference(_Unreachable(), FakePromptGuard()), Bm25Retriever())

        with pytest.raises(LoopFailedError, match="model server") as raised:
            await loop.run(QUESTION, CORPUS, report)

        assert "10.0.0.7" not in str(raised.value)
        assert trail(reported) == [(0, "planner", "error")]
        assert "10.0.0.7" not in (reported[0].error or "")

    async def test_a_corpus_with_nothing_to_retrieve_fails_before_any_model_call(self):
        h = Harness(accepted_first_time())

        async def ignore(step: AgentStep) -> None:
            return None

        with pytest.raises(LoopFailedError, match="included"):
            await h.loop.run(QUESTION, (), ignore)

        assert h.provider.requests == []


class TestLimits:
    @pytest.mark.parametrize("rounds", [0, 11])
    def test_max_rounds_is_between_one_and_ten(self, rounds: int):
        inference = GuardedInference(FakeInferenceProvider([]), FakePromptGuard())

        with pytest.raises(ValueError, match="max_rounds"):
            BriefLoop(inference, Bm25Retriever(), max_rounds=rounds)
