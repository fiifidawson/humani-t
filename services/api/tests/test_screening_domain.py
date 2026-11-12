"""Requirements for screening, discovery and the run manifest, as rules with no I/O.

A decision records its score, rationale and provenance (model id, prompt version, criteria
version), so a reviewer can reconstruct it later, and `RunManifest` records what makes a
run reproducible.
"""

from __future__ import annotations

import math
import string
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.discovery import (
    IngestedText,
    RunPaper,
    TextKind,
    identity_digest,
    merge_found,
    screening_text,
)
from app.domain.manifest import StageSummary, assemble_manifest
from app.domain.paper import PaperRecord
from app.domain.runs import Run, RunState
from app.domain.screening import (
    MAX_QUOTE_LENGTH,
    UNREADABLE_RATIONALE,
    CriterionAnswer,
    CriterionVerdict,
    Passage,
    PassageSourceKind,
    Priority,
    ScreeningDecision,
    ScreeningLabel,
    ScreeningTier,
    exclusion_reasons,
    passage_tag,
    prisma_counts,
)

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
INCLUSION = ("population", "intervention", "outcome")
EXCLUSION = ("not-humanitarian",)


@pytest.fixture
def decision() -> ScreeningDecision:
    return ScreeningDecision(
        run_id=uuid.uuid4(),
        paper_id=uuid.uuid4(),
        label=ScreeningLabel.INCLUDE,
        score=0.9,
        priority=Priority.HIGH,
        rationale="Displaced households received unconditional cash; food security measured.",
        criteria_matched=("population", "intervention", "outcome"),
        exclusion_reasons=(),
        model_id="llama3.1:8b",
        prompt_version="screen-1",
        criteria_version=3,
        evidence_kind=TextKind.ABSTRACT,
        decided_at=NOW,
    )


def _record(
    external_id: str, title: str, *, doi: str | None = None, source: str = "a"
) -> PaperRecord:
    return PaperRecord(
        source_id=source, external_id=external_id, title=title, year=2023, doi=doi
    )


class TestScreeningDecision:
    def test_a_valid_decision_keeps_its_provenance(self, decision):
        assert (decision.model_id, decision.prompt_version, decision.criteria_version) == (
            "llama3.1:8b",
            "screen-1",
            3,
        )

    @pytest.mark.parametrize("score", [-0.01, 1.01, math.nan, math.inf])
    def test_a_score_outside_zero_to_one_is_refused(self, decision, score):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.score"):
            replace(decision, score=score)

    def test_a_blank_rationale_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.rationale"):
            replace(decision, rationale="  ")

    def test_a_decision_without_a_model_id_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.model_id"):
            replace(decision, model_id=" ")

    def test_a_decision_without_a_prompt_version_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.prompt_version"):
            replace(decision, prompt_version="")

    def test_a_criteria_version_below_one_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.criteria_version"):
            replace(decision, criteria_version=0)

    def test_a_naive_decision_time_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.decided_at"):
            replace(decision, decided_at=datetime(2026, 11, 3, 9, 0))  # noqa: DTZ001

    def test_a_criterion_named_twice_is_refused(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.criteria_matched"):
            replace(decision, criteria_matched=("population", "population"))

    def test_an_exclusion_must_carry_its_reasons(self, decision):
        """PRISMA counts exclusions by reason; an exclusion without one cannot be counted."""
        with pytest.raises(ValueError, match=r"ScreeningDecision\.exclusion_reasons"):
            replace(decision, label=ScreeningLabel.EXCLUDE, exclusion_reasons=())

    def test_only_an_exclusion_carries_exclusion_reasons(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.exclusion_reasons"):
            replace(decision, exclusion_reasons=("outcome",))

    def test_an_unreadable_answer_is_recorded_as_uncertain_with_its_reason(self, decision):
        fallback = ScreeningDecision.unreadable(
            run_id=decision.run_id,
            paper_id=decision.paper_id,
            model_id="llama3.1:8b",
            prompt_version="screen-1",
            criteria_version=3,
            evidence_kind=TextKind.ABSTRACT,
            decided_at=NOW,
        )

        assert fallback.label is ScreeningLabel.UNCERTAIN
        assert fallback.is_unreadable
        assert fallback.rationale == UNREADABLE_RATIONALE
        assert "could not be read" in UNREADABLE_RATIONALE
        assert fallback.model_id == "llama3.1:8b"

    def test_only_an_uncertain_decision_can_be_unreadable(self, decision):
        with pytest.raises(ValueError, match=r"ScreeningDecision\.is_unreadable"):
            replace(decision, is_unreadable=True)


class TestTiers:
    """The names people see follow from label and priority alone."""

    @pytest.mark.parametrize(
        ("priority", "tier"),
        [
            (Priority.HIGH, ScreeningTier.HIGHLY_RELEVANT),
            (Priority.MEDIUM, ScreeningTier.RELEVANT),
            (Priority.LOW, ScreeningTier.POSSIBLY_RELEVANT),
        ],
    )
    def test_an_inclusion_is_tiered_by_its_priority(self, decision, priority, tier):
        assert replace(decision, priority=priority).tier is tier

    @pytest.mark.parametrize("priority", list(Priority))
    def test_an_exclusion_is_excluded_whatever_its_priority(self, decision, priority):
        excluded = replace(
            decision,
            label=ScreeningLabel.EXCLUDE,
            priority=priority,
            exclusion_reasons=("not-humanitarian",),
        )

        assert excluded.tier is ScreeningTier.EXCLUDED

    def test_an_uncertain_decision_is_uncertain(self, decision):
        assert replace(decision, label=ScreeningLabel.UNCERTAIN).tier is ScreeningTier.UNCERTAIN


class TestLabelRules:
    """A label must agree with the criteria it names, or the answer is not readable."""

    def test_an_inclusion_has_no_exclusion_reasons(self):
        reasons = exclusion_reasons(
            ScreeningLabel.INCLUDE, ("population", "intervention"), INCLUSION, EXCLUSION
        )

        assert reasons == ()

    def test_an_inclusion_that_meets_an_exclusion_criterion_contradicts_itself(self):
        with pytest.raises(ValueError, match="exclusion criterion"):
            exclusion_reasons(
                ScreeningLabel.INCLUDE, ("population", "not-humanitarian"), INCLUSION, EXCLUSION
            )

    def test_an_exclusion_names_the_exclusion_criteria_met_and_the_inclusion_criteria_missed(
        self,
    ):
        reasons = exclusion_reasons(
            ScreeningLabel.EXCLUDE, ("population", "not-humanitarian"), INCLUSION, EXCLUSION
        )

        assert reasons == ("not-humanitarian", "intervention", "outcome")

    def test_an_exclusion_with_nothing_against_the_paper_contradicts_itself(self):
        with pytest.raises(ValueError, match="excluded"):
            exclusion_reasons(ScreeningLabel.EXCLUDE, INCLUSION, INCLUSION, EXCLUSION)

    def test_a_criterion_that_is_not_in_the_plan_is_refused(self):
        with pytest.raises(ValueError, match="unknown"):
            exclusion_reasons(ScreeningLabel.INCLUDE, ("invented",), INCLUSION, EXCLUSION)

    def test_an_uncertain_decision_has_no_exclusion_reasons(self):
        assert exclusion_reasons(ScreeningLabel.UNCERTAIN, (), INCLUSION, EXCLUSION) == ()


TEXT = (
    "Unconditional cash for displaced households in [LOCATION_1] cut acute malnutrition.\n"
    "Food security improved within six months, and school attendance rose."
)


def _located(quote: str | None, text: str = TEXT) -> str | None:
    return CriterionAnswer.located("I1", CriterionVerdict.YES, quote, text).quote


class TestQuotes:
    """The stored quote is the paper's own characters, whatever the model's wording."""

    def test_a_quote_copied_exactly_is_kept(self):
        assert _located("cash for displaced households") == "cash for displaced households"

    def test_a_quote_that_rejoins_a_line_break_keeps_the_papers_line_break(self):
        assert _located("acute malnutrition. Food security") == (
            "acute malnutrition.\nFood security"
        )

    def test_a_quote_in_another_case_is_kept_in_the_papers_case(self):
        assert _located("CASH FOR Displaced HOUSEHOLDS") == "cash for displaced households"

    @pytest.mark.parametrize("ellipsis", ["…", "...", " … ", " ... "])
    def test_pieces_joined_by_an_ellipsis_are_kept_as_the_papers_own_pieces(self, ellipsis):
        quote = f"unconditional cash for displaced{ellipsis}food security improved"

        assert _located(quote) == "Unconditional cash for displaced … Food security improved"

    def test_a_quote_past_the_limit_is_kept_shortened_at_a_word(self):
        text = " ".join(f"word{n}" for n in range(200))

        quote = _located(text.upper(), text)

        assert quote is not None
        assert len(quote) <= MAX_QUOTE_LENGTH
        assert quote.endswith("…")
        assert text.startswith(quote.removesuffix("…"))
        assert text[len(quote) - 1] == " "

    def test_no_quote_is_none(self):
        assert _located(None) is None

    def test_a_paraphrase_is_refused(self):
        with pytest.raises(ValueError, match="I1"):
            _located("cash transfers reduced malnutrition")

    def test_pieces_out_of_order_are_refused(self):
        with pytest.raises(ValueError, match="I1"):
            _located("food security improved … unconditional cash for displaced")

    def test_a_piece_shorter_than_three_words_is_refused(self):
        with pytest.raises(ValueError, match="three words"):
            _located("unconditional cash for displaced … food security")

    def test_short_common_words_cannot_match_by_accident(self):
        with pytest.raises(ValueError, match="three words"):
            _located("for … in … and")

    @pytest.mark.parametrize("quote", ["", "   ", "…", "..."])
    def test_an_empty_quote_is_refused(self, quote):
        with pytest.raises(ValueError, match="I1"):
            _located(quote)


_WORDS = st.lists(
    st.text(alphabet=string.ascii_letters + string.digits + ",;:()-'", min_size=1, max_size=9),
    min_size=3,
    max_size=90,
)


class TestQuoteProperties:
    @given(_WORDS, st.sampled_from([" ", "\n", "  "]), st.data())
    def test_every_stored_piece_is_an_exact_piece_of_the_text(self, words, gap, data):
        text = gap.join(words)
        bounds = sorted(data.draw(st.lists(st.integers(0, len(words)), min_size=4, max_size=4)))
        pieces = [p for p in (words[bounds[0] : bounds[1]], words[bounds[2] : bounds[3]]) if p]
        assume(pieces)
        assume(len(pieces) == 1 or all(len(p) >= 3 for p in pieces))
        ellipsis = data.draw(st.sampled_from(["…", " … ", "..."]))
        quote = ellipsis.join(" ".join(p) for p in pieces).swapcase()

        stored = _located(quote, text)

        assert stored is not None
        for piece in stored.removesuffix("…").split(" … "):
            assert piece in text


class TestPassages:
    def test_a_tag_is_the_papers_place_in_the_run(self):
        assert [passage_tag(n) for n in (1, 2, 31)] == ["P1", "P2", "P31"]

    def test_a_tag_needs_a_place_from_one(self):
        with pytest.raises(ValueError, match="position"):
            passage_tag(0)

    def test_a_passage_needs_text(self):
        with pytest.raises(ValueError, match=r"Passage\.text"):
            Passage("P1", uuid.uuid4(), PassageSourceKind.PAPER, " ", "abstract")

    def test_a_passage_needs_a_tag(self):
        with pytest.raises(ValueError, match=r"Passage\.tag"):
            Passage("", uuid.uuid4(), PassageSourceKind.PAPER, "text", "abstract")


class TestDiscovery:
    def test_merging_keeps_one_record_per_paper_and_who_found_it(self):
        merged = merge_found(
            [
                ("openalex", [_record("W1", "Cash in camps", doi="10.1/x", source="openalex")]),
                (
                    "europepmc",
                    [
                        _record(
                            "PMC9",
                            "CASH IN CAMPS",
                            doi="https://doi.org/10.1/X",
                            source="europepmc",
                        ),
                        _record("PMC8", "Vouchers", source="europepmc"),
                    ],
                ),
            ]
        )

        assert [(m.record.external_id, m.found_by) for m in merged] == [
            ("W1", ("openalex", "europepmc")),
            ("PMC8", ("europepmc",)),
        ]

    def test_the_first_source_listed_wins_a_duplicate(self):
        merged = merge_found(
            [
                ("arxiv", [_record("A1", "Same", source="arxiv")]),
                ("openalex", [_record("W1", "Same", source="openalex")]),
            ]
        )

        assert [m.record.source_id for m in merged] == ["arxiv"]

    def test_an_identity_digest_depends_on_the_key(self):
        """The digest stands in for the identity in storage; without the key it says nothing."""
        identity = _record("W1", "Cash", doi="10.1/x").identity

        assert identity_digest(b"k" * 32, identity) == identity_digest(b"k" * 32, identity)
        assert identity_digest(b"k" * 32, identity) != identity_digest(b"j" * 32, identity)
        assert identity.encode() not in identity_digest(b"k" * 32, identity)

    def test_the_text_to_screen_is_the_title_and_abstract(self):
        record = replace(_record("W1", "Cash in camps"), abstract="Households in Marib.")

        assert screening_text(record) == (
            TextKind.ABSTRACT,
            "Cash in camps\n\nHouseholds in Marib.",
        )

    def test_a_paper_without_an_abstract_has_only_its_title(self):
        assert screening_text(_record("W1", "Cash in camps")) == (
            TextKind.TITLE,
            "Cash in camps",
        )

    def test_a_run_paper_counts_its_place_from_one(self):
        with pytest.raises(ValueError, match=r"RunPaper\.position"):
            RunPaper(uuid.uuid4(), uuid.uuid4(), 0, ("openalex",))

    def test_a_run_paper_names_who_found_it(self):
        with pytest.raises(ValueError, match=r"RunPaper\.found_by"):
            RunPaper(uuid.uuid4(), uuid.uuid4(), 1, ())

    def test_ingested_text_must_not_be_blank(self):
        with pytest.raises(ValueError, match=r"IngestedText\.text"):
            IngestedText(uuid.uuid4(), TextKind.ABSTRACT, " ")


class TestPrisma:
    def test_counts_follow_the_papers_through_each_step(self, decision):
        excluded = replace(
            decision,
            paper_id=uuid.uuid4(),
            label=ScreeningLabel.EXCLUDE,
            exclusion_reasons=("not-humanitarian", "outcome"),
        )
        uncertain = replace(decision, paper_id=uuid.uuid4(), label=ScreeningLabel.UNCERTAIN)

        counts = prisma_counts(
            identified_by_source={"openalex": 6, "europepmc": 4},
            failed_sources=("arxiv",),
            after_deduplication=8,
            decisions=[decision, excluded, uncertain],
        )

        assert counts.identified == 10
        assert counts.duplicates_removed == 2
        assert counts.after_deduplication == 8
        assert counts.screened == 3
        assert (counts.included, counts.excluded, counts.uncertain) == (1, 1, 1)
        assert counts.not_screened == 5
        assert counts.excluded_by_reason == {"not-humanitarian": 1, "outcome": 1}
        assert counts.failed_sources == ("arxiv",)

    def test_more_papers_after_deduplication_than_identified_is_impossible(self):
        with pytest.raises(ValueError, match="after_deduplication"):
            prisma_counts(
                identified_by_source={"openalex": 1},
                failed_sources=(),
                after_deduplication=2,
                decisions=[],
            )

    def test_more_decisions_than_papers_is_impossible(self, decision):
        with pytest.raises(ValueError, match="decisions"):
            prisma_counts(
                identified_by_source={"openalex": 0},
                failed_sources=(),
                after_deduplication=0,
                decisions=[decision],
            )


def _run(stages: tuple[str, ...] = ("queries", "discover", "screen")) -> Run:
    return Run(
        id=uuid.uuid4(),
        review_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        stages=stages,
        state=RunState.DONE,
        created_at=NOW,
        created_by=uuid.uuid4(),
        queued_at=NOW,
        stages_finished=len(stages),
        started_at=NOW,
        finished_at=NOW,
    )


class TestRunManifest:
    """Assembled from what each stage put under "manifest" in its summary, plus the run."""

    def test_the_facts_of_every_stage_are_gathered(self):
        run = _run()
        manifest = assemble_manifest(
            run,
            [
                StageSummary("queries", 0.4, {"manifest": {"vocabulary_version": 2}}),
                StageSummary(
                    "discover",
                    3.0,
                    {
                        "found": 4,
                        "manifest": {
                            "vocabulary_version": 2,
                            "sources": ["openalex", "arxiv"],
                            "queries": {"openalex": '("cash")', "arxiv": '("cash")'},
                        },
                    },
                ),
                StageSummary(
                    "screen",
                    60.0,
                    {
                        "manifest": {
                            "model_id": "llama3.1:8b",
                            "prompt_version": "screen-1",
                            "criteria_version": 3,
                        }
                    },
                ),
            ],
        )

        assert manifest.run_id == run.id
        assert manifest.vocabulary_versions == (2,)
        assert manifest.criteria_versions == (3,)
        assert manifest.sources == ("openalex", "arxiv")
        assert manifest.queries == {"openalex": '("cash")', "arxiv": '("cash")'}
        assert manifest.model_ids == ("llama3.1:8b",)
        assert manifest.prompt_versions == {"screen": "screen-1"}
        assert [s.stage for s in manifest.stage_facts] == ["queries", "discover", "screen"]
        assert manifest.is_complete

    def test_named_prompts_and_several_models_are_kept_apart(self):
        manifest = assemble_manifest(
            _run(("answer",)),
            [
                StageSummary(
                    "answer",
                    1.0,
                    {
                        "manifest": {
                            "model_ids": ["qwen3:8b", "llama3.1:8b"],
                            "prompt_versions": {"writer": "w-2", "critic": "c-1"},
                        }
                    },
                )
            ],
        )

        assert manifest.model_ids == ("llama3.1:8b", "qwen3:8b")
        assert manifest.prompt_versions == {"answer.critic": "c-1", "answer.writer": "w-2"}

    def test_two_stages_that_disagree_on_a_version_both_show(self):
        """Never silently pick one: a run that used two vocabularies must say so."""
        manifest = assemble_manifest(
            _run(("queries", "discover")),
            [
                StageSummary("queries", 1.0, {"manifest": {"vocabulary_version": 2}}),
                StageSummary("discover", 1.0, {"manifest": {"vocabulary_version": 3}}),
            ],
        )

        assert manifest.vocabulary_versions == (2, 3)

    def test_a_stage_without_manifest_facts_is_listed_with_none(self):
        manifest = assemble_manifest(
            _run(("queries",)), [StageSummary("queries", 1.0, {"items": 2})]
        )

        assert manifest.stage_facts[0].facts == {}

    def test_a_run_that_stopped_early_is_not_complete(self):
        manifest = assemble_manifest(
            _run(("queries", "discover")), [StageSummary("queries", 1.0, {})]
        )

        assert not manifest.is_complete
