"""The decision brief's rules, as plain domain types with no I/O.

Sources: architecture §4.2 (options, trade-offs, conflicts, considerations,
gaps; evidence and hypothesis claims; code checks the citations; no winner),
§4.4 (an `AgentStep`'s fields) and UI_UX_SPEC §6.9, §8.1 to §8.3 and §8.7.

Each test builds a valid brief and perturbs one thing.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.briefs import (
    CAVEAT,
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
    DecisionBrief,
    EvidenceStrength,
    Gap,
    Option,
    Problem,
    RetrievedPassage,
    SourceDetails,
    SourceKind,
    StepStatus,
    StopReason,
    TradeOff,
    body_from_dict,
    body_to_dict,
    check_brief,
    check_citations,
)
from app.domain.runs import agent_step_payload
from app.domain.screening import Passage

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def evidence(text: str, *tags: str) -> Claim:
    return Claim(
        text, ClaimSupport.EVIDENCE, tuple(Citation(t, SourceKind.PAPER) for t in tags)
    )


def reasoning(text: str) -> Claim:
    return Claim(text, ClaimSupport.HYPOTHESIS)


def option(name: str, strength: EvidenceStrength = EvidenceStrength.MODERATE) -> Option:
    return Option(
        name=name,
        description=f"{name}, as delivered in the camps.",
        strength=strength,
        benefits=(evidence(f"{name} improves diet diversity", "P0001"),),
        risks=(reasoning(f"{name} may strain local supply"),),
    )


def row(criterion: str, *names: str) -> TradeOff:
    return TradeOff(
        criterion,
        tuple(Assessment(n, evidence(f"{n} on {criterion}", "P0002")) for n in names),
    )


def body(**changes: Any) -> BriefBody:
    names = ("Cash transfers", "In-kind food")
    values: dict[str, Any] = {
        "bottom_line": (
            evidence("Cash improves food security where markets work.", "P0001"),
            reasoning("Evidence for camps with weak markets is thin."),
        ),
        "options": tuple(option(n) for n in names),
        "trade_offs": (row("Do no harm", *names), row("Cost to deliver", *names)),
        "conflicts": (
            Conflict(
                "Do cash transfers raise local food prices?",
                ConflictType.CONTEXT,
                (evidence("Yes, in small markets", "P0001"), evidence("No", "P0002")),
                reasoning("The effect likely depends on market size."),
            ),
        ),
        "considerations": (
            Consideration(
                "Do no harm", "In-kind food", evidence("No cash is carried", "P0002")
            ),
        ),
        "gaps": (Gap("Nothing on people with disabilities."),),
    }
    values.update(changes)
    return BriefBody(**values)


RETRIEVED = frozenset({"P0001", "P0002"})


class TestClaims:
    def test_an_evidence_claim_must_cite_at_least_one_passage(self):
        with pytest.raises(ValueError, match=r"Claim\.citations"):
            Claim("Cash improves diet", ClaimSupport.EVIDENCE)

    def test_a_hypothesis_claim_must_not_cite_anything(self):
        with pytest.raises(ValueError, match=r"Claim\.citations"):
            Claim(
                "Supports local traders",
                ClaimSupport.HYPOTHESIS,
                (Citation("P0001", SourceKind.PAPER),),
            )

    def test_claim_text_must_not_be_blank(self):
        with pytest.raises(ValueError, match=r"Claim\.text"):
            reasoning("   ")

    def test_a_claim_cites_each_passage_once(self):
        with pytest.raises(ValueError, match=r"Claim\.citations"):
            evidence("Prices rise", "P0001", "P0001")

    def test_a_citation_tag_must_not_be_blank(self):
        with pytest.raises(ValueError, match=r"Citation\.tag"):
            Citation(" ", SourceKind.PAPER)


class TestOptions:
    @pytest.mark.parametrize("count", [2, 3, 4])
    def test_a_brief_compares_between_two_and_four_options(self, count: int):
        names = [f"Option {i}" for i in range(count)]

        brief = body(
            options=tuple(option(n) for n in names),
            trade_offs=(row("Do no harm", *names),),
            considerations=(),
        )

        assert len(brief.options) == count

    @pytest.mark.parametrize("count", [1, 5])
    def test_fewer_than_two_or_more_than_four_options_are_refused(self, count: int):
        names = [f"Option {i}" for i in range(count)]

        with pytest.raises(ValueError, match=r"BriefBody\.options"):
            body(
                options=tuple(option(n) for n in names),
                trade_offs=(row("Do no harm", *names),),
                considerations=(),
            )

    def test_option_names_are_unique(self):
        with pytest.raises(ValueError, match=r"BriefBody\.options"):
            body(options=(option("Cash transfers"), option("cash transfers")))

    @pytest.mark.parametrize(
        "name", ["Cash transfers (recommended)", "Winner: vouchers", "#1 In-kind food"]
    )
    def test_an_option_name_must_not_label_it_the_winner(self, name: str):
        with pytest.raises(ValueError, match=r"Option\.name"):
            option(name)


class TestTradeOffs:
    def test_every_trade_off_row_has_one_assessment_per_option(self):
        with pytest.raises(ValueError, match=r"TradeOff"):
            body(trade_offs=(row("Do no harm", "Cash transfers"),))

    def test_a_row_may_not_assess_an_option_twice(self):
        with pytest.raises(ValueError, match=r"TradeOff"):
            body(trade_offs=(row("Do no harm", "Cash transfers", "Cash transfers"),))

    def test_a_row_may_not_assess_an_option_the_brief_does_not_compare(self):
        with pytest.raises(ValueError, match=r"TradeOff"):
            body(trade_offs=(row("Do no harm", "Cash transfers", "Vouchers"),))

    @pytest.mark.parametrize("criterion", ["Do no harm", "IMPARTIALITY", " neutrality "])
    def test_humanitarian_principles_are_recognised_whatever_the_case(self, criterion: str):
        assert row(criterion, "A", "B").is_humanitarian

    def test_other_criteria_are_not_humanitarian_principles(self):
        assert not row("Cost to deliver", "A", "B").is_humanitarian


class TestConflictsAndConsiderations:
    def test_a_conflict_needs_at_least_two_positions(self):
        with pytest.raises(ValueError, match=r"Conflict\.positions"):
            Conflict("Do prices rise?", ConflictType.NOISE, (evidence("Yes", "P0001"),))

    def test_what_a_conflict_means_is_reasoning_not_evidence(self):
        with pytest.raises(ValueError, match=r"Conflict\.meaning"):
            Conflict(
                "Do prices rise?",
                ConflictType.HIERARCHY,
                (evidence("Yes", "P0001"), evidence("No", "P0002")),
                evidence("It depends", "P0001"),
            )

    def test_a_consideration_must_name_an_option_in_the_brief(self):
        with pytest.raises(ValueError, match=r"Consideration"):
            body(considerations=(Consideration("Cost", "Vouchers", reasoning("Cheaper")),))


class TestBottomLine:
    @pytest.mark.parametrize("sentences", [0, 1, 4])
    def test_the_bottom_line_is_two_or_three_claims(self, sentences: int):
        claims = tuple(reasoning(f"Sentence {i}.") for i in range(sentences))

        with pytest.raises(ValueError, match=r"BriefBody\.bottom_line"):
            body(bottom_line=claims)


class TestNoWinner:
    _FORBIDDEN = ("winner", "rank", "recommend", "total", "best", "preferred", "overall")
    _TYPES = (
        BriefBody,
        Option,
        Claim,
        Citation,
        TradeOff,
        Assessment,
        Conflict,
        Consideration,
        Gap,
        CriticScores,
        DecisionBrief,
    )

    def test_no_field_names_a_winner_a_rank_or_a_total_score(self):
        fields = [f.name for t in self._TYPES for f in dataclasses.fields(t)]

        assert [f for f in fields if any(word in f for word in self._FORBIDDEN)] == []

    def test_the_critics_scores_have_no_total(self):
        scores = CriticScores(2, 2, 1, 2, 2, 1)

        assert not hasattr(scores, "total")


class TestCriticScores:
    @pytest.mark.parametrize("value", [-1, 3])
    def test_each_score_is_zero_one_or_two(self, value: int):
        with pytest.raises(ValueError, match=r"CriticScores\.coverage"):
            CriticScores(value, 2, 2, 2, 2, 2)


class TestCitationCheck:
    def test_a_brief_citing_only_retrieved_passages_has_no_citation_problems(self):
        assert check_citations(body(), RETRIEVED) == ()

    def test_a_citation_of_a_passage_that_was_never_retrieved_is_a_problem(self):
        invented = body(gaps=(), bottom_line=(evidence("Prices rise", "P9999"), reasoning("x")))

        problems = check_citations(invented, RETRIEVED)

        assert [(p.code, p.where) for p in problems] == [
            ("citation_not_retrieved", "bottom_line/0")
        ]
        assert "P9999" in problems[0].message

    def test_every_claim_in_the_brief_is_checked(self):
        brief = body()

        problems = check_citations(brief, frozenset())

        cited = {c.tag for _, claim in brief.claims() for c in claim.citations}
        assert {p.code for p in problems} == {"citation_not_retrieved"}
        assert len(problems) == sum(len(c.citations) for _, c in brief.claims())
        assert cited == RETRIEVED


class TestBriefChecks:
    def test_a_well_formed_brief_has_no_problems(self):
        assert check_brief(body(), RETRIEVED) == ()

    def test_a_brief_without_a_humanitarian_criterion_has_a_problem(self):
        names = ("Cash transfers", "In-kind food")

        problems = check_brief(body(trade_offs=(row("Cost", *names),)), RETRIEVED)

        assert [p.code for p in problems] == ["no_humanitarian_criterion"]

    def test_an_option_rated_on_evidence_with_no_cited_claim_has_a_problem(self):
        unsourced = replace(
            option("Cash transfers", EvidenceStrength.WEAK),
            benefits=(reasoning("Households choose"),),
        )

        problems = check_brief(body(options=(unsourced, option("In-kind food"))), RETRIEVED)

        assert [(p.code, p.where) for p in problems] == [
            ("option_without_evidence", "options/0")
        ]

    def test_an_option_with_insufficient_evidence_needs_no_cited_claim(self):
        unsourced = replace(
            option("Cash transfers", EvidenceStrength.INSUFFICIENT),
            benefits=(reasoning("Households choose"),),
        )

        assert check_brief(body(options=(unsourced, option("In-kind food"))), RETRIEVED) == ()

    def test_unretrieved_citations_are_among_the_checks(self):
        problems = check_brief(body(), frozenset({"P0001"}))

        assert {p.code for p in problems} == {"citation_not_retrieved"}


class TestBodyStorage:
    def test_a_body_survives_a_round_trip_through_its_stored_form(self):
        brief = body()

        assert body_from_dict(body_to_dict(brief)) == brief

    def test_claims_are_listed_with_where_they_appear(self):
        refs = [ref.path for ref, _ in body().claims()]

        assert refs[:2] == ["bottom_line/0", "bottom_line/1"]
        assert "options/0/benefits/0" in refs
        assert "trade_offs/1/assessments/1" in refs
        assert "conflicts/0/positions/1" in refs
        assert "conflicts/0/meaning" in refs
        assert "considerations/0/because" in refs

    def test_a_claims_place_names_its_section_option_and_criterion(self):
        places = {ref.path: ref for ref, _ in body().claims()}

        risk = places["options/1/risks/0"]
        cell = places["trade_offs/0/assessments/0"]
        assert (risk.section, risk.option, risk.criterion) == ("risk", "In-kind food", None)
        assert (cell.section, cell.option, cell.criterion) == (
            "trade_off",
            "Cash transfers",
            "Do no harm",
        )


def step(**changes: Any) -> AgentStep:
    values: dict[str, Any] = {
        "round_number": 1,
        "role": AgentRole.WRITER,
        "status": StepStatus.OK,
        "seconds": 1.5,
    }
    values.update(changes)
    return AgentStep(**values)


class TestAgentStep:
    def test_the_planner_step_is_round_zero(self):
        with pytest.raises(ValueError, match=r"AgentStep\.round"):
            step(role=AgentRole.PLANNER, round_number=1)

    def test_a_step_that_did_not_succeed_says_why(self):
        with pytest.raises(ValueError, match=r"AgentStep\.error"):
            step(status=StepStatus.INVALID)

    def test_a_step_is_a_valid_agent_step_event(self):
        payload = step(
            prompt_tokens=120,
            completion_tokens=40,
            prompt_version="brief-writer/1",
            output={"options": ["Cash transfers", "In-kind food"]},
        ).to_payload()

        assert agent_step_payload(payload) == payload
        assert (payload["round"], payload["role"], payload["status"]) == (1, "writer", "ok")
        assert payload["tokens"] == {"prompt": 120, "completion": 40}

    def test_the_checks_step_carries_its_problems(self):
        problem = Problem("citation_not_retrieved", "P9999 was never retrieved.", "gaps/0")

        payload = step(role=AgentRole.CHECKS, problems=(problem,)).to_payload()

        assert payload["problems"] == [
            {
                "code": "citation_not_retrieved",
                "message": "P9999 was never retrieved.",
                "where": "gaps/0",
            }
        ]

    def test_a_step_survives_a_round_trip_through_its_payload(self):
        original = step(
            role=AgentRole.RETRIEVER,
            queries=("cash transfers prices",),
            passages_found=4,
            passages_new=3,
            passages_total=7,
        )

        assert AgentStep.from_payload(original.to_payload()) == original


def brief(**changes: Any) -> DecisionBrief:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "review_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "version": 1,
        "question": "Should we switch to cash in the camps near [LOCATION_1]?",
        "body": body(),
        "model_id": "qwen3.5:9b",
        "rounds": 2,
        "kept_round": 2,
        "max_rounds": 3,
        "stopped_because": StopReason.ACCEPTED,
        "created_at": NOW,
    }
    values.update(changes)
    return DecisionBrief(**values)


class TestDecisionBrief:
    def test_a_brief_carries_the_fixed_caveat(self):
        assert brief().caveat == CAVEAT

    def test_the_caveat_cannot_be_blank(self):
        with pytest.raises(ValueError, match=r"DecisionBrief\.caveat"):
            brief(caveat=" ")

    def test_the_kept_round_is_one_of_the_rounds_run(self):
        with pytest.raises(ValueError, match=r"DecisionBrief\.kept_round"):
            brief(rounds=2, kept_round=3)

    def test_no_more_rounds_run_than_the_limit(self):
        with pytest.raises(ValueError, match=r"DecisionBrief\.rounds"):
            brief(rounds=4, kept_round=1, max_rounds=3)

    def test_the_question_must_not_be_blank(self):
        with pytest.raises(ValueError, match=r"DecisionBrief\.question"):
            brief(question="")


class TestPassages:
    def test_a_retrieved_passage_records_the_search_that_found_it(self):
        passage = Passage("P1", uuid.uuid4(), SourceKind.PAPER, "Prices rose.", "abstract")

        with pytest.raises(ValueError, match=r"RetrievedPassage\.found_by"):
            RetrievedPassage(passage, found_by=" ", round_number=0)

    def test_source_details_need_a_title(self):
        with pytest.raises(ValueError, match=r"SourceDetails\.title"):
            SourceDetails(title=" ")
