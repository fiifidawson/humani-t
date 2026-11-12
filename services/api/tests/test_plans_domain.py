"""Requirements for a review's search plan, as pure domain rules.

Plan versions are frozen and record their origin, and a user-supplied plan (route B) never
touches the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.masking import EntityType, MaskingRule
from app.domain.plans import (
    AuthorKind,
    ChangeKind,
    ChangeRequest,
    ClarifyingQuestion,
    Criterion,
    CriterionKind,
    PlanAuthor,
    PlanConflictError,
    PlanConversation,
    PlanMode,
    ReviewPlan,
    VocabularyFormat,
    diff_plans,
    first_stage_to_redo,
    number_criteria,
    parse_strict_vocabulary,
    plan_text_fields,
    refuse_rule_terms,
    revision_from_gate,
    revision_text_fields,
    term_authors,
)
from app.domain.query import compile_vocabulary
from app.domain.reviews import ReviewStatus, ReviewTrigger, next_review_status
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
REVIEW_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
KOFI = uuid.uuid4()
MODEL = PlanAuthor.model()

CASH = Concept("cash transfers", ("cash assistance",), theme="intervention")
FOOD = Concept("food security", ("hunger",), theme="outcome")
CRITERIA = number_criteria(
    [
        (CriterionKind.INCLUSION, "Studies of cash or voucher programmes"),
        (CriterionKind.EXCLUSION, "Opinion pieces"),
    ]
)


def draft(
    *concepts: Concept,
    version: int = 1,
    author: PlanAuthor = MODEL,
    origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED,
    criteria: tuple[Criterion, ...] = CRITERIA,
) -> ReviewPlan:
    is_model = author.kind is AuthorKind.MODEL
    return ReviewPlan(
        review_id=REVIEW_ID,
        version=version,
        vocabulary=Vocabulary(concepts or (CASH, FOOD), origin, version=version),
        criteria=criteria,
        author=author,
        created_at=NOW,
        model_id="llama3.1:8b" if is_model else None,
        prompt_version="plan.draft@1" if is_model else None,
    )


def keys(diff_terms):
    return {(t.concept, t.term, t.change) for t in diff_terms}


class TestCriteria:
    def test_criteria_are_numbered_per_kind_in_the_order_given(self):
        numbered = number_criteria(
            [
                (CriterionKind.INCLUSION, "a"),
                (CriterionKind.EXCLUSION, "b"),
                (CriterionKind.INCLUSION, "c"),
            ]
        )

        assert [(c.id, c.text) for c in numbered] == [("I1", "a"), ("E1", "b"), ("I2", "c")]

    def test_a_blank_criterion_is_refused(self):
        with pytest.raises(ValueError, match=r"Criterion.text"):
            Criterion("I1", CriterionKind.INCLUSION, "   ")

    def test_a_criterion_is_trimmed(self):
        assert Criterion("I1", CriterionKind.INCLUSION, "  RCTs  ").text == "RCTs"


class TestAuthors:
    def test_a_person_author_names_the_person(self):
        with pytest.raises(ValueError, match="user_id"):
            PlanAuthor(AuthorKind.PERSON, None)

    def test_the_model_is_not_a_person(self):
        with pytest.raises(ValueError, match="user_id"):
            PlanAuthor(AuthorKind.MODEL, KOFI)


class TestReviewPlan:
    def test_the_vocabulary_carries_the_plans_version(self):
        with pytest.raises(ValueError, match="version"):
            ReviewPlan(
                review_id=REVIEW_ID,
                version=2,
                vocabulary=Vocabulary((CASH,), VocabularyOrigin.LLM_ASSISTED, version=1),
                criteria=CRITERIA,
                author=PlanAuthor.person(KOFI),
                created_at=NOW,
            )

    def test_a_version_the_model_wrote_records_which_model_and_prompt(self):
        with pytest.raises(ValueError, match="model_id"):
            ReviewPlan(
                review_id=REVIEW_ID,
                version=1,
                vocabulary=Vocabulary((CASH,), VocabularyOrigin.LLM_ASSISTED),
                criteria=CRITERIA,
                author=MODEL,
                created_at=NOW,
            )

    def test_a_strict_vocabulary_can_never_be_written_by_the_model(self):
        """Route B never routes through the model."""
        with pytest.raises(ValueError, match="user-supplied"):
            draft(CASH, origin=VocabularyOrigin.USER_SUPPLIED)

    def test_accepting_freezes_the_vocabulary_at_this_version(self):
        accepted = draft().accepted(by=KOFI, at=NOW)

        assert accepted.is_accepted
        assert accepted.vocabulary.is_frozen
        assert accepted.vocabulary.frozen_at == NOW
        assert accepted.vocabulary.version == accepted.version == 1
        assert accepted.accepted_by == KOFI

    def test_an_accepted_version_cannot_be_accepted_again(self):
        accepted = draft().accepted(by=KOFI, at=NOW)

        with pytest.raises(PlanConflictError):
            accepted.accepted(by=uuid.uuid4(), at=NOW + timedelta(minutes=1))

    def test_editing_an_accepted_version_makes_the_next_one_and_leaves_it_untouched(self):
        accepted = draft().accepted(by=KOFI, at=NOW)

        following = accepted.followed_by(
            concepts=(CASH,),
            criteria=CRITERIA,
            origin=VocabularyOrigin.LLM_ASSISTED,
            author=PlanAuthor.person(KOFI),
            at=NOW + timedelta(minutes=5),
        )

        assert following.version == 2
        assert following.vocabulary.version == 2
        assert not following.is_accepted
        assert not following.vocabulary.is_frozen
        assert accepted.vocabulary.concepts == (CASH, FOOD)
        assert accepted.is_accepted

    def test_accepted_at_and_accepted_by_are_set_together(self):
        with pytest.raises(ValueError, match="accepted"):
            ReviewPlan(
                review_id=REVIEW_ID,
                version=1,
                vocabulary=Vocabulary((CASH,), VocabularyOrigin.USER_SUPPLIED),
                criteria=CRITERIA,
                author=PlanAuthor.person(KOFI),
                created_at=NOW,
                accepted_at=NOW,
            )

    def test_a_frozen_vocabulary_belongs_only_to_an_accepted_version(self):
        with pytest.raises(ValueError, match="frozen"):
            ReviewPlan(
                review_id=REVIEW_ID,
                version=1,
                vocabulary=Vocabulary((CASH,), VocabularyOrigin.USER_SUPPLIED).freeze(at=NOW),
                criteria=CRITERIA,
                author=PlanAuthor.person(KOFI),
                created_at=NOW,
            )

    def test_the_change_request_that_produced_a_version_came_before_it(self):
        request = ChangeRequest(
            id=uuid.uuid4(),
            text="add mobile money",
            requested_by=KOFI,
            requested_at=NOW,
            based_on_version=2,
        )

        with pytest.raises(ValueError, match="based_on_version"):
            ReviewPlan(
                review_id=REVIEW_ID,
                version=2,
                vocabulary=Vocabulary((CASH,), VocabularyOrigin.LLM_ASSISTED, version=2),
                criteria=CRITERIA,
                author=MODEL,
                created_at=NOW,
                change_request=request,
                model_id="m",
                prompt_version="p",
            )


class TestDiff:
    def test_the_first_version_adds_everything_and_says_who_wrote_it(self):
        diff = diff_plans(None, draft())

        assert diff.from_version is None
        assert diff.to_version == 1
        assert keys(diff.terms) == {
            ("cash transfers", "cash transfers", ChangeKind.ADDED),
            ("cash transfers", "cash assistance", ChangeKind.ADDED),
            ("food security", "food security", ChangeKind.ADDED),
            ("food security", "hunger", ChangeKind.ADDED),
        }
        assert {t.by for t in diff.terms} == {MODEL}
        assert len(diff.criteria) == 2

    def test_a_new_synonym_is_one_added_term_credited_to_whoever_wrote_the_version(self):
        before = draft()
        cash_with_mobile = Concept(
            "cash transfers", ("cash assistance", "mobile money"), theme="intervention"
        )
        after = draft(cash_with_mobile, FOOD, version=2, author=PlanAuthor.person(KOFI))

        diff = diff_plans(before, after)

        assert keys(diff.terms) == {("cash transfers", "mobile money", ChangeKind.ADDED)}
        assert diff.terms[0].by == PlanAuthor.person(KOFI)
        assert diff.concepts == ()

    def test_a_removed_concept_takes_its_terms_with_it(self):
        diff = diff_plans(draft(), draft(CASH, version=2))

        assert [(c.term, c.change) for c in diff.concepts] == [
            ("food security", ChangeKind.REMOVED)
        ]
        assert keys(diff.terms) == {
            ("food security", "food security", ChangeKind.REMOVED),
            ("food security", "hunger", ChangeKind.REMOVED),
        }

    def test_terms_are_compared_without_regard_to_case(self):
        shouting = Concept("Cash Transfers", ("CASH ASSISTANCE",), theme="Intervention")

        diff = diff_plans(draft(CASH), draft(shouting, version=2))

        assert diff.is_empty

    def test_criteria_changes_are_listed(self):
        fewer = CRITERIA[:1]

        diff = diff_plans(draft(), draft(version=2, criteria=fewer))

        assert [(c.text, c.change) for c in diff.criteria] == [
            ("Opinion pieces", ChangeKind.REMOVED)
        ]

    def test_the_same_plan_twice_differs_in_nothing(self):
        assert diff_plans(draft(), draft(version=2)).is_empty


class TestTermAuthors:
    def test_a_term_keeps_the_author_of_the_version_that_introduced_it(self):
        mobile = Concept(
            "cash transfers", ("cash assistance", "mobile money"), theme="intervention"
        )
        versions = [
            draft(),
            draft(mobile, FOOD, version=2, author=PlanAuthor.person(KOFI)),
            draft(mobile, FOOD, version=3),
        ]

        authors = term_authors(versions)

        assert authors[("intervention", "cash transfers", "mobile money")] == PlanAuthor.person(
            KOFI
        )
        assert authors[("intervention", "cash transfers", "cash assistance")] == MODEL

    def test_a_term_dropped_and_added_back_belongs_to_whoever_added_it_back(self):
        versions = [
            draft(),
            draft(CASH, version=2),
            draft(CASH, FOOD, version=3, author=PlanAuthor.person(KOFI)),
        ]

        authors = term_authors(versions)

        assert authors[("outcome", "food security", "hunger")] == PlanAuthor.person(KOFI)


class TestStrictTextFormat:
    def test_lines_become_concepts_in_the_theme_above_them(self):
        concepts = parse_strict_vocabulary(
            "[intervention]\ncash transfers; cash assistance\n[outcome]\nfood security\n",
            VocabularyFormat.TEXT,
        )

        assert concepts == (
            Concept("cash transfers", ("cash assistance",), theme="intervention"),
            Concept("food security", (), theme="outcome"),
        )

    def test_terms_reach_the_vocabulary_exactly_as_written(self):
        concepts = parse_strict_vocabulary(
            "[Setting]\nIDP camp*; Internally-Displaced Persons (IDPs)\n",
            VocabularyFormat.TEXT,
        )

        assert concepts[0].terms == ("IDP camp*", "Internally-Displaced Persons (IDPs)")
        assert concepts[0].theme == "Setting"

    def test_comments_and_blank_lines_are_ignored(self):
        concepts = parse_strict_vocabulary(
            "# my validated list\n\n  \n[x]\nalpha\n", VocabularyFormat.TEXT
        )

        assert [c.term for c in concepts] == ["alpha"]

    def test_lines_before_any_theme_belong_to_the_general_theme(self):
        concepts = parse_strict_vocabulary("alpha; beta\n", VocabularyFormat.TEXT)

        assert concepts[0].theme == "general"

    def test_an_empty_term_is_refused_with_its_line_number(self):
        with pytest.raises(ValueError, match="line 2"):
            parse_strict_vocabulary("[x]\nalpha;;beta\n", VocabularyFormat.TEXT)

    def test_a_term_repeated_within_one_concept_is_refused_not_dropped(self):
        with pytest.raises(ValueError, match=r"line 1.*twice"):
            parse_strict_vocabulary("cash; Cash\n", VocabularyFormat.TEXT)

    def test_a_concept_repeated_within_one_theme_is_refused(self):
        with pytest.raises(ValueError, match="line 3"):
            parse_strict_vocabulary("[x]\ncash\ncash; money\n", VocabularyFormat.TEXT)

    def test_an_upload_with_no_terms_is_refused(self):
        with pytest.raises(ValueError, match="no terms"):
            parse_strict_vocabulary("# nothing here\n[x]\n", VocabularyFormat.TEXT)

    def test_a_term_with_a_control_character_is_refused(self):
        with pytest.raises(ValueError, match="line 1"):
            parse_strict_vocabulary("cash\x00money\n", VocabularyFormat.TEXT)

    def test_a_strict_vocabulary_compiles_to_exactly_the_supplied_terms(self):
        """The invariant behind route B: nothing gained, nothing lost, nothing respelled."""
        supplied = ["cash transfers", "Cash-Based Assistance", "food security", "hunger"]
        concepts = parse_strict_vocabulary(
            "[a]\ncash transfers; Cash-Based Assistance\n[b]\nfood security; hunger\n",
            VocabularyFormat.TEXT,
        )

        query = compile_vocabulary(Vocabulary(concepts, VocabularyOrigin.USER_SUPPLIED))

        assert list(query.all_terms) == supplied


class TestStrictCsvFormat:
    def test_rows_become_concepts(self):
        concepts = parse_strict_vocabulary(
            "theme,term,synonyms\nintervention,cash transfers,cash assistance;vouchers\n"
            "outcome,food security,\n",
            VocabularyFormat.CSV,
        )

        assert concepts == (
            Concept("cash transfers", ("cash assistance", "vouchers"), theme="intervention"),
            Concept("food security", (), theme="outcome"),
        )

    def test_a_quoted_field_keeps_its_comma(self):
        concepts = parse_strict_vocabulary(
            'theme,term,synonyms\nx,"water, sanitation and hygiene",WASH\n',
            VocabularyFormat.CSV,
        )

        assert concepts[0].terms == ("water, sanitation and hygiene", "WASH")

    def test_the_header_is_required(self):
        with pytest.raises(ValueError, match="header"):
            parse_strict_vocabulary("x,cash,money\n", VocabularyFormat.CSV)

    def test_a_row_without_a_term_is_refused_with_its_line_number(self):
        with pytest.raises(ValueError, match="line 3"):
            parse_strict_vocabulary(
                "theme,term,synonyms\nx,cash,\ny,,money\n", VocabularyFormat.CSV
            )


class TestConversation:
    def conversation(self) -> PlanConversation:
        return PlanConversation(
            review_id=REVIEW_ID,
            project_id=PROJECT_ID,
            mode=PlanMode.DEEP,
            created_at=NOW,
            created_by=KOFI,
            questions=(
                ClarifyingQuestion("q1", "Which outcomes matter most?", ("Prices",)),
                ClarifyingQuestion("q2", "Which settings?", ()),
            ),
        )

    def test_answering_every_question_makes_it_ready_to_draft(self):
        conversation = self.conversation()

        answered = conversation.with_answers({"q1": "Prices", "q2": None}, by=KOFI, at=NOW)

        assert not conversation.is_ready_to_draft
        assert answered.is_ready_to_draft
        assert answered.questions[0].answer == "Prices"
        assert answered.questions[1].is_skipped

    def test_an_unknown_question_is_refused(self):
        with pytest.raises(ValueError, match="q9"):
            self.conversation().with_answers({"q9": "x"}, by=KOFI, at=NOW)

    def test_skipping_the_rest_settles_every_open_question(self):
        settled = self.conversation().with_rest_skipped(by=KOFI, at=NOW)

        assert settled.is_ready_to_draft
        assert all(q.is_skipped for q in settled.questions)

    def test_a_blank_answer_is_refused(self):
        with pytest.raises(ValueError, match="answer"):
            self.conversation().with_answers({"q1": "  "}, by=KOFI, at=NOW)


class TestChangeRequest:
    def test_a_blank_change_request_is_refused(self):
        with pytest.raises(ValueError, match=r"ChangeRequest.text"):
            ChangeRequest(uuid.uuid4(), "  ", KOFI, NOW, based_on_version=1)

    def test_a_change_request_is_trimmed(self):
        request = ChangeRequest(uuid.uuid4(), " add vouchers ", KOFI, NOW, based_on_version=1)

        assert request.text == "add vouchers"


class TestGateRevisions:
    def test_revisions_give_the_edited_concepts_and_criteria(self):
        concepts, criteria = revision_from_gate(
            {
                "vocabulary": {
                    "concepts": [{"term": "cash", "synonyms": ["money"], "theme": "x"}]
                },
                "criteria": [{"kind": "inclusion", "text": "RCTs"}],
            }
        )

        assert concepts == (Concept("cash", ("money",), theme="x"),)
        assert criteria is not None
        assert [(c.id, c.text) for c in criteria] == [("I1", "RCTs")]

    def test_revisions_may_change_only_the_vocabulary(self):
        concepts, criteria = revision_from_gate(
            {"vocabulary": {"concepts": [{"term": "cash"}]}}
        )

        assert concepts == (Concept("cash"),)
        assert criteria is None

    def test_revisions_with_nothing_usable_are_refused(self):
        with pytest.raises(ValueError, match="revisions"):
            revision_from_gate({"note": "looks fine"})

    def test_a_malformed_concept_is_refused(self):
        with pytest.raises(ValueError, match="term"):
            revision_from_gate({"vocabulary": {"concepts": [{"synonyms": ["x"]}]}})


class TestMaskedTermsInPlanText:
    """Plan text a person writes reaches the model and the paper sources unmasked."""

    CAMP = MaskingRule(uuid.uuid4(), "Hagadera", EntityType.LOCATION)

    def test_a_criterion_holding_a_masked_term_is_refused_naming_it_and_not_the_term(self):
        criteria = number_criteria(
            [
                (CriterionKind.INCLUSION, "Refugee settings"),
                (CriterionKind.EXCLUSION, "Reports from the hagadera office"),
            ]
        )

        with pytest.raises(ValueError, match="Criterion E1") as refused:
            refuse_rule_terms(plan_text_fields((CASH,), criteria), [self.CAMP])

        assert "agadera" not in str(refused.value)

    @pytest.mark.parametrize(
        ("concept", "field"),
        [
            (Concept("camps", ("Hagadera camp",), theme="setting"), "Concept 2"),
            (Concept("camps", theme="Hagadera"), "Concept 2"),
        ],
        ids=["synonym", "theme"],
    )
    def test_a_search_term_or_theme_holding_a_masked_term_is_refused(self, concept, field):
        with pytest.raises(ValueError, match=field):
            refuse_rule_terms(plan_text_fields((CASH, concept), CRITERIA), [self.CAMP])

    def test_a_term_inside_a_longer_word_is_not_a_masked_term(self):
        concepts = (Concept("Hagaderas", theme="setting"),)

        refuse_rule_terms(plan_text_fields(concepts, CRITERIA), [self.CAMP])

    def test_gate_revisions_are_refused_naming_the_path_of_the_text(self):
        revisions = {
            "vocabulary": {"concepts": [{"term": "cash", "synonyms": ["vouchers"]}]},
            "criteria": [{"kind": "inclusion", "text": "RCTs"}, {"text": "Not Hagadera"}],
        }

        with pytest.raises(ValueError, match=r"revisions\.criteria\[1\]\.text"):
            refuse_rule_terms(revision_text_fields(revisions), [self.CAMP])


class TestReviewStates:
    """Editing an accepted plan makes a new version."""

    @pytest.mark.parametrize(
        "status",
        [ReviewStatus.ACCEPTED, ReviewStatus.DONE, ReviewStatus.FAILED, ReviewStatus.CANCELLED],
    )
    def test_a_review_at_rest_may_ask_for_changes(self, status):
        assert (
            next_review_status(status, ReviewTrigger.REQUEST_CHANGES)
            is ReviewStatus.CHANGES_REQUESTED
        )


class TestWhereARunAfterAChangeStarts:
    """Prompt-first changes after a run (Wave 2): the person only says what to
    change, and the next run starts from the earliest stage the change affects.
    A changed search finds different papers, so it starts again from the search;
    changed criteria re-judge the papers already found; nothing changed, nothing
    to redo.
    """

    def test_an_added_term_searches_again(self):
        before = draft()
        after = draft(
            Concept(
                "cash transfers", ("cash assistance", "mobile money"), theme="intervention"
            ),
            FOOD,
            version=2,
        )

        assert first_stage_to_redo(before, after) == "queries"

    def test_a_removed_concept_searches_again(self):
        before = draft(CASH, FOOD, Concept("Tigray", (), theme="setting"))
        after = draft(CASH, FOOD, version=2)

        assert first_stage_to_redo(before, after) == "queries"

    def test_changed_criteria_alone_rescreen_the_papers_already_found(self):
        before = draft()
        stricter = number_criteria(
            [
                (CriterionKind.INCLUSION, "Studies of cash or voucher programmes"),
                (CriterionKind.EXCLUSION, "Opinion pieces"),
                (CriterionKind.EXCLUSION, "Published before 2015"),
            ]
        )
        after = draft(version=2, criteria=stricter)

        assert first_stage_to_redo(before, after) == "screen"

    def test_a_change_to_terms_and_criteria_searches_again(self):
        before = draft()
        after = draft(
            CASH,
            Concept("food security", ("hunger", "dietary diversity"), theme="outcome"),
            version=2,
            criteria=number_criteria([(CriterionKind.INCLUSION, "Any cash study")]),
        )

        assert first_stage_to_redo(before, after) == "queries"

    def test_nothing_changed_redoes_nothing(self):
        assert first_stage_to_redo(draft(), draft(version=2)) is None
