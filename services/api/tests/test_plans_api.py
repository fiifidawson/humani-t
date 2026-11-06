"""Requirements for the search-planning conversation, through the HTTP API.

Sources: architecture §4.3 (routes A and B; frozen, versioned; origin recorded;
route B never touches the model), §3.9 (the question, answers and change
requests are masked before any model call), §5.4 (sealed under the project's
key), §5.7 (the first accept wins); UI_UX_SPEC §5.3.2 (privacy receipt),
§5.3.3 (clarifying questions), §5.3.4 (draft and accept), §6.5 (compare with
the previous draft, who added each term), §11.2 (a second accept gets 409).

Every model call goes through `GuardedInference` to a `FakeInferenceProvider`;
nothing in this file can reach a network.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.domain.encryption import KeyDestroyedError
from app.domain.inference import InferenceUnavailableError
from tests.plan_support import (
    DRAFT,
    REVISED,
    PlanTeam,
    make_plan_team,
    stored_versions,
)
from tests.project_support import PROJECTS, project_events, send


@pytest.fixture
def team() -> PlanTeam:
    return make_plan_team()


def _terms(version: dict) -> dict[str, str]:
    """Every term of a version, mapped to who added it."""
    return {
        term["text"]: term["added_by"]["kind"]
        for theme in version["themes"]
        for concept in theme["concepts"]
        for term in concept["terms"]
    }


def _deep_and_answered(team: PlanTeam) -> dict:
    started = team.start("deep")
    assert started.status_code == 201, started.text
    ids = [q["id"] for q in started.json()["questions"]]
    answered = team.answer({ids[0]: "Prices", ids[1]: "Camps near Aden", ids[2]: None})
    assert answered.status_code == 200, answered.text
    body: dict = answered.json()
    return body


class TestStartingAPlan:
    def test_deep_mode_asks_clarifying_questions_before_drafting(self, team):
        response = team.start("deep")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "clarifying"
        assert body["mode"] == "deep"
        assert body["questions"][0]["text"] == "Which outcomes matter most?"
        assert body["questions"][0]["suggestions"] == ["Food security", "Prices"]
        assert body["current"] is None

    def test_quick_mode_drafts_at_once(self, team):
        body = team.drafted("shallow")

        assert body["status"] == "drafting"
        current = body["current"]
        assert current["version"] == 1
        assert current["origin"] == "llm_assisted"
        assert current["is_strict"] is False
        assert current["author"] == {"kind": "model", "user_id": None}
        assert current["model_id"] == "planner-test-model"
        assert current["prompt_version"].startswith("plan.draft@")
        assert [c["id"] for c in current["criteria"]] == ["I1", "E1"]
        assert current["query"]["rendered"] == (
            '("cash transfers" OR "cash assistance") AND ("malnutrition" OR "wasting")'
        )

    def test_a_plan_is_started_once(self, team):
        team.drafted()

        assert team.start("deep").status_code == 409

    def test_the_question_is_masked_before_the_model_sees_it(self, team):
        team.drafted()

        sent = team.model.sent_text()
        assert "Marib" not in sent
        assert "[LOCATION_1]" in sent
        assert team.sanitizer.inputs[0].endswith("in Marib?")

    def test_a_call_the_prompt_guard_refuses_sends_nothing_and_changes_nothing(self):
        team = make_plan_team(refusal="1 PHONE found")

        response = team.start("shallow")

        assert response.status_code == 422
        assert team.model.requests == []
        body = team.plan()
        assert body["status"] == "new"
        assert body["receipts"] == []

    def test_an_unreachable_model_changes_nothing_but_says_the_text_may_have_left(self, team):
        team.model.failure = InferenceUnavailableError("connection refused")

        response = team.start("shallow")

        assert response.status_code == 502
        body = team.plan()
        assert body["status"] == "new"
        assert body["current"] is None
        assert [r["delivered"] for r in body["receipts"]] == [False]


class TestAnswers:
    def test_answers_are_kept_until_every_question_is_settled(self, team):
        ids = [q["id"] for q in team.start("deep").json()["questions"]]
        calls = len(team.model.requests)

        response = team.answer({ids[0]: "Prices"})

        assert response.status_code == 200
        assert response.json()["status"] == "clarifying"
        assert response.json()["questions"][0]["answer"] == "Prices"
        assert len(team.model.requests) == calls

    def test_the_last_answer_drafts_the_plan_with_the_answers_it_used(self, team):
        body = _deep_and_answered(team)

        assert body["status"] == "drafting"
        clarifications = body["current"]["clarifications"]
        assert [(c["answer"], c["skipped"]) for c in clarifications] == [
            ("Prices", False),
            ("Camps near Aden", False),
            (None, True),
        ]

    def test_answers_are_masked_before_the_model_sees_them(self, team):
        _deep_and_answered(team)

        sent = team.model.sent_text()
        assert "Aden" not in sent
        assert "Camps near [LOCATION_" in sent

    def test_skip_all_and_draft_now(self, team):
        team.start("deep")

        response = team.answer({}, draft_now=True)

        assert response.status_code == 200
        assert response.json()["status"] == "drafting"
        assert all(q["skipped"] for q in response.json()["questions"])

    def test_an_unknown_question_is_refused(self, team):
        team.start("deep")

        assert team.answer({"q9": "x"}).status_code == 422

    def test_answers_are_refused_when_no_questions_are_open(self, team):
        team.drafted()

        assert team.answer({"q1": "x"}).status_code == 409


class TestChanges:
    def test_a_change_request_makes_the_next_version_and_records_why(self, team):
        team.drafted()

        response = team.change("add mobile money as a synonym for cash", based_on_version=1)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "drafting"
        assert body["pending_change"] is None
        current = body["current"]
        assert current["version"] == 2
        assert current["change_request"]["text"] == "add mobile money as a synonym for cash"
        assert current["change_request"]["based_on_version"] == 1
        assert [v["version"] for v in body["versions"]] == [1, 2]

    def test_the_change_request_is_masked_before_the_model_sees_it(self, team):
        team.drafted()

        team.change("drop anything about Dr Amina Yusuf's clinic", based_on_version=1)

        sent = team.model.sent_text()
        assert "Amina" not in sent
        assert "[PERSON_1]" in sent

    def test_a_change_to_a_draft_that_has_moved_on_is_refused(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)

        assert team.change("add vouchers", based_on_version=1).status_code == 409

    def test_the_model_never_changes_a_strict_vocabulary(self, team):
        assert team.upload().status_code == 200
        calls = len(team.model.requests)

        response = team.change("add synonyms", based_on_version=1)

        assert response.status_code == 409
        assert len(team.model.requests) == calls

    def test_changing_an_accepted_plan_makes_a_new_draft_and_keeps_the_accepted_one(self, team):
        team.drafted()
        team.accept(1)

        response = team.change("add mobile money", based_on_version=1)

        body = response.json()
        assert body["status"] == "drafting"
        assert body["current"]["version"] == 2
        assert body["current"]["accepted_at"] is None
        assert body["accepted_version"] == 1

    def test_model_terms_that_hold_masked_text_are_dropped_and_noted(self):
        leaky = {
            **DRAFT,
            "themes": [
                *DRAFT["themes"],
                {
                    "name": "setting",
                    "concepts": [{"term": "displacement", "synonyms": ["[LOCATION_1]"]}],
                },
            ],
        }
        team = make_plan_team(drafts=[leaky])

        current = team.drafted()["current"]

        assert "[LOCATION_1]" not in _terms(current)
        assert "displacement" in _terms(current)
        assert any("masked" in note for note in current["notes"])


class TestAccept:
    def test_accepting_freezes_the_vocabulary_at_this_version(self, team):
        team.drafted()

        response = team.accept(1)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["accepted_version"] == 1
        assert body["current"]["accepted_at"] is not None
        assert body["current"]["accepted_by"] == team.rt.team.user_id("amara@example.org")
        assert body["current"]["query"]["provenance"]["vocabulary_version"] == 1

    def test_a_second_accept_gets_409_and_the_first_stands(self, team):
        team.drafted()
        team.accept(1)

        second = team.accept(1, browser=team.reviewer)

        assert second.status_code == 409
        assert "already accepted" in second.json()["detail"]
        accepted_by = team.plan()["current"]["accepted_by"]
        assert accepted_by == team.rt.team.user_id("amara@example.org")

    def test_only_the_latest_draft_can_be_accepted(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)

        assert team.accept(1).status_code == 409


class TestStrictUpload:
    def test_an_uploaded_vocabulary_is_used_exactly_as_given_and_never_reaches_the_model(
        self, team
    ):
        response = team.upload()

        assert response.status_code == 200
        current = response.json()["current"]
        assert current["origin"] == "user_supplied"
        assert current["is_strict"] is True
        assert current["author"]["kind"] == "person"
        assert current["model_id"] is None
        assert current["query"]["rendered"] == (
            '("cash transfers" OR "Cash-Based Assistance") AND ("wasting")'
        )
        assert team.model.requests == []
        assert team.sanitizer.inputs == []
        assert response.json()["receipts"] == []

    def test_a_csv_upload_is_read_the_same_way(self, team):
        csv = "theme,term,synonyms\nintervention,cash transfers,vouchers;mobile money\n"

        current = team.upload(csv, format="csv").json()["current"]

        assert set(_terms(current)) == {"cash transfers", "vouchers", "mobile money"}

    def test_a_malformed_upload_is_refused_naming_the_line(self, team):
        response = team.upload("[x]\ncash;;money\n")

        assert response.status_code == 422
        assert "line 2" in response.json()["detail"]

    def test_criteria_come_with_the_upload(self, team):
        criteria = [{"kind": "inclusion", "text": "Randomised trials"}]

        current = team.upload(criteria=criteria).json()["current"]

        assert [(c["id"], c["text"]) for c in current["criteria"]] == [
            ("I1", "Randomised trials")
        ]

    def test_an_uploaded_vocabulary_is_accepted_like_any_other(self, team):
        team.upload()

        assert team.accept(1).status_code == 200
        assert team.model.requests == []


class TestVersions:
    def test_a_version_carries_its_diff_against_the_one_before(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)

        response = team.owner.get(team.path("/versions/2"))

        assert response.status_code == 200
        diff = response.json()["diff"]
        assert diff["from_version"] == 1
        assert [(t["term"], t["change"], t["by"]["kind"]) for t in diff["terms"]] == [
            ("mobile money", "added", "model")
        ]
        assert response.json()["plan"]["version"] == 2

    def test_the_first_version_differs_from_nothing(self, team):
        team.drafted()

        diff = team.owner.get(team.path("/versions/1")).json()["diff"]

        assert diff["from_version"] is None
        assert {t["change"] for t in diff["terms"]} == {"added"}

    def test_a_missing_version_is_404(self, team):
        team.drafted()

        assert team.owner.get(team.path("/versions/7")).status_code == 404

    def test_each_term_says_who_added_it(self, team):
        team.drafted()
        team.upload("[intervention]\ncash transfers; cash assistance; vouchers\n")

        terms = _terms(team.plan()["current"])

        assert terms["cash assistance"] == "model"
        assert terms["vouchers"] == "person"


class TestPrivacyReceipts:
    def test_each_model_call_has_a_receipt_of_exactly_what_was_sent(self, team):
        _deep_and_answered(team)

        receipts = team.plan()["receipts"]

        assert [r["purpose"] for r in receipts] == ["clarify", "draft"]
        clarify, draft = receipts
        assert clarify["items"][0]["kind"] == "question"
        assert clarify["items"][0]["sent"].endswith("in [LOCATION_1]?")
        assert clarify["items"][0]["counts"] == {"LOCATION": 1}
        assert clarify["masked_total"] == 1
        assert [i["kind"] for i in draft["items"]] == ["question", "answer", "answer"]
        assert draft["items"][2]["sent"] == "Camps near [LOCATION_1]"
        assert all(r["delivered"] for r in receipts)
        assert all(r["model_id"] == "planner-test-model" for r in receipts)

    def test_a_change_request_has_its_receipt(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)

        receipts = team.plan()["receipts"]

        assert receipts[-1]["purpose"] == "revise"
        assert receipts[-1]["items"][-1] == {
            "kind": "change_request",
            "ref": None,
            "sent": "add mobile money",
            "counts": {},
        }


class TestAccess:
    def test_a_viewer_reads_the_plan_but_cannot_change_it(self, team):
        team.drafted()

        assert team.viewer.get(team.path()).status_code == 200
        assert team.viewer.get(team.path("/versions/1")).status_code == 200
        assert team.accept(1, browser=team.viewer).status_code == 403
        assert team.change("x", 1, browser=team.viewer).status_code == 403
        assert team.upload(browser=team.viewer).status_code == 403

    def test_a_reviewer_may_plan(self, team):
        assert team.start("shallow", browser=team.reviewer).status_code == 201
        assert team.accept(1, browser=team.reviewer).status_code == 200

    def test_a_non_member_learns_nothing(self, team):
        team.drafted()

        assert team.outsider.get(team.path()).status_code == 404
        assert team.start(browser=team.outsider).status_code == 404


class TestWhatIsStored:
    def test_nothing_the_plan_says_is_stored_in_the_clear(self, team):
        _deep_and_answered(team)
        team.change("add mobile money", based_on_version=1)

        stored = team.stored_bytes()

        for plaintext in (
            "Marib",
            "Aden",
            "cash transfers",
            "mobile money",
            "Which outcomes",
            "Opinion pieces",
            "LOCATION_1",
        ):
            assert plaintext.encode() not in stored

    def test_deleting_the_project_makes_the_plan_unreadable(self, team):
        team.drafted()

        deleted = send(team.owner, "DELETE", f"{PROJECTS}/{team.rt.project_id}")

        assert deleted.status_code == 204

        async def read() -> None:
            async with team.rt.harness.providers.storage.unit_of_work() as uow:
                record = (await uow.plans.list_versions(UUID(team.review_id)))[0]
                await team.plans.open_version(uow, record)

        with pytest.raises(KeyDestroyedError):
            asyncio.run(read())

    def test_the_audit_log_records_ids_only(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)
        team.accept(2)

        events = [
            e
            for e in project_events(team.rt.harness, team.rt.project_id)
            if "plan" in e.entry.action
        ]

        assert [e.entry.action for e in events] == [
            "plan.started",
            "plan.changes_requested",
            "plan.revised",
            "plan.accepted",
        ]
        assert all(e.entry.target_id == team.review_id for e in events)
        assert all(e.entry.details_sealed is None for e in events)

    def test_a_version_is_stored_with_its_author_and_origin(self, team):
        team.drafted()
        team.upload()

        versions = stored_versions(team)

        assert [(v.origin.value, v.author.kind.value) for v in versions] == [
            ("llm_assisted", "model"),
            ("user_supplied", "person"),
        ]


def test_revised_draft_fixture_adds_exactly_one_synonym():
    """Guard for the specs above: they rely on REVISED adding "mobile money" only."""
    before = DRAFT["themes"][0]["concepts"][0]["synonyms"]
    after = REVISED["themes"][0]["concepts"][0]["synonyms"]
    assert [s for s in after if s not in before] == ["mobile money"]


class TestReceiptsNameTheirVersion:
    """A receipt names the draft or change request it belongs to: none is matched by place."""

    def test_clarifying_questions_belong_to_no_version(self, team):
        _deep_and_answered(team)

        clarify, draft = team.plan()["receipts"]

        assert (clarify["plan_version"], clarify["change_request_id"]) == (None, None)
        assert (draft["plan_version"], draft["change_request_id"]) == (1, None)

    def test_a_revision_names_the_version_it_wrote_and_the_request_behind_it(self, team):
        team.drafted()
        body = team.change("add mobile money", based_on_version=1).json()

        revise = body["receipts"][-1]

        assert revise["plan_version"] == 2
        assert revise["change_request_id"] == body["current"]["change_request"]["id"]

    def test_a_failed_draft_still_names_the_version_it_was_writing(self, team):
        team.model.failure = InferenceUnavailableError("connection refused")
        team.start("shallow")

        (receipt,) = team.plan()["receipts"]

        assert receipt["delivered"] is False
        assert receipt["plan_version"] == 1

    def test_a_failed_revision_still_names_its_change_request(self, team):
        team.drafted()
        team.model.failure = InferenceUnavailableError("connection refused")

        assert team.change("add mobile money", based_on_version=1).status_code == 502

        body = team.plan()
        failed = body["receipts"][-1]
        assert failed["delivered"] is False
        assert failed["plan_version"] == 2
        assert failed["change_request_id"] == body["pending_change"]["id"]


class TestOlderVersionsStayReadable:
    """A decision cites its `criteria_version`; that version must be readable for good."""

    def test_an_accepted_version_is_readable_after_a_newer_one_is_written(self, team):
        team.drafted()
        assert team.accept(1).status_code == 200
        team.change("add mobile money", based_on_version=1)

        first = team.owner.get(team.path("/versions/1"))

        assert first.status_code == 200
        plan = first.json()["plan"]
        assert plan["version"] == 1
        assert plan["accepted_at"] is not None
        assert [c["id"] for c in plan["criteria"]] == [
            c["id"] for c in team.plan()["current"]["criteria"]
        ][: len(plan["criteria"])]

    def test_a_viewer_reads_an_older_version_and_an_outsider_gets_404(self, team):
        team.drafted()
        team.change("add mobile money", based_on_version=1)

        assert team.viewer.get(team.path("/versions/1")).status_code == 200
        assert team.outsider.get(team.path("/versions/1")).status_code == 404
