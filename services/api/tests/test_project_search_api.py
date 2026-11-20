"""Requirements for `GET /api/v1/search`, across the caller's projects (UI_UX_SPEC §7.6).

Results are grouped by project and come only from projects the caller is a member of. It
reads the text members already see: names, questions, briefs, paper records, the brief's
masked passages and follow-up questions. Raw document text is never searched (ADR 0018).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.application.project_search import SEARCHES_PER_MINUTE
from app.domain.project_search import MAX_QUERY_LENGTH
from tests.brief_support import write_brief
from tests.document_support import make_document_team
from tests.pipeline_support import make_pipeline
from tests.project_support import (
    PROJECT_NAME,
    VIEWER_EMAIL,
    Team,
    audit_events,
    create_project,
    make_team,
    send,
)
from tests.question_support import make_ask_team
from tests.run_support import QUESTION, make_run_team, run_until_idle

SEARCH = "/api/v1/search"


def search(browser: Any, q: str, scope: str | None = None) -> Any:
    params = {"q": q} if scope is None else {"q": q, "scope": scope}
    return browser.get(SEARCH, params=params)


def found(browser: Any, q: str, scope: str | None = None) -> dict[str, Any]:
    response = search(browser, q, scope)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def hits(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for g in body["groups"] for h in g["hits"]]


@pytest.fixture
def team() -> Team:
    return make_team()


class TestWhoFindsWhat:
    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_a_member_finds_the_project_by_its_name_grouped_under_it(self, team, role):
        body = found(team.browser_for(role), "cholera")

        assert body["groups"] == [
            {
                "project_id": team.project_id,
                "project_name": PROJECT_NAME,
                "is_archived": False,
                "hits": [
                    {
                        "scope": "projects",
                        "text": PROJECT_NAME,
                        "review_id": None,
                        "run_id": None,
                        "brief_version": None,
                        "paper_id": None,
                        "tag": None,
                        "title": None,
                        "thread_id": None,
                        "question_id": None,
                    }
                ],
            }
        ]
        assert body["is_truncated"] is False

    def test_a_non_member_finds_nothing_in_a_project_whose_name_matches(self, team):
        body = found(team.outsider, "cholera")

        assert body["groups"] == []

    def test_an_administrator_who_is_not_a_member_finds_nothing(self, team):
        assert found(team.admin, "cholera")["groups"] == []

    def test_each_person_finds_only_their_own_projects(self, team):
        create_project(team.outsider, "Cholera vaccination, Aden")

        mine = found(team.owner, "cholera")
        theirs = found(team.outsider, "cholera")

        assert [g["project_name"] for g in mine["groups"]] == [PROJECT_NAME]
        assert [g["project_name"] for g in theirs["groups"]] == ["Cholera vaccination, Aden"]

    def test_a_member_who_was_removed_no_longer_finds_the_project(self, team):
        viewer_id = team.user_id(VIEWER_EMAIL)
        removed = send(team.owner, "DELETE", team.path(f"/members/{viewer_id}"))
        assert removed.status_code == 204, removed.text

        assert found(team.viewer, "cholera")["groups"] == []

    def test_a_deleted_project_is_not_searched(self, team):
        deleted = send(team.owner, "DELETE", team.path())
        assert deleted.status_code == 204, deleted.text

        assert found(team.owner, "cholera")["groups"] == []

    def test_an_archived_project_is_found_and_marked(self, team):
        archived = send(team.owner, "POST", team.path("/archive"))
        assert archived.status_code == 200, archived.text

        [group] = found(team.owner, "cholera")["groups"]

        assert group["is_archived"] is True

    def test_signing_in_is_required(self, team):
        assert search(team.harness.browser(), "cholera").status_code == 401


class TestWhatIsSearched:
    def test_a_review_question_is_found_and_names_its_review(self):
        rt = make_run_team()

        [hit] = hits(found(rt.viewer, "malnutrition displaced", "projects"))

        assert hit["scope"] == "projects"
        assert hit["text"] == QUESTION
        assert hit["review_id"] == rt.review_id

    def test_a_project_is_found_by_its_tags(self, team):
        owner = team.browser_for("owner")
        path = f"/api/v1/projects/{team.project_id}/tags"
        assert send(owner, "PUT", path, {"tags": ["Sahel", "WASH"]}).status_code == 200

        [hit] = hits(found(team.browser_for("viewer"), "sahel", "projects"))

        assert hit["scope"] == "projects"
        assert hit["text"] == "Sahel"

    def test_the_claims_of_the_latest_brief_are_found_with_its_run_and_version(self):
        rt = make_run_team()
        run_id = write_brief(rt)

        [hit] = hits(found(rt.viewer, "prices small markets", "briefs"))

        assert hit["text"] == "Prices can rise in small markets"
        assert (hit["review_id"], hit["run_id"], hit["brief_version"]) == (
            rt.review_id,
            run_id,
            1,
        )

    def test_the_masked_passages_a_brief_cites_are_found_by_their_tag(self):
        rt = make_run_team()
        run_id = write_brief(rt)

        [hit] = hits(found(rt.viewer, "staple food prices", "passages"))

        assert hit["tag"] == "P0001"
        assert hit["run_id"] == run_id
        assert "[LOCATION_1]" in hit["text"]

    def test_papers_are_found_by_title_and_by_abstract(self):
        pipeline = make_pipeline()
        run_id = pipeline.rt.start_run(["discover"])
        run_until_idle(pipeline.rt.worker(pipeline.stages()))

        by_title = hits(found(pipeline.rt.viewer, "food vouchers", "papers"))
        by_abstract = hits(found(pipeline.rt.viewer, "non-crisis city", "papers"))

        assert [h["title"] for h in by_title] == ["Food vouchers in displacement camps"]
        assert [h["title"] for h in by_abstract] == ["Cash transfers in stable urban markets"]
        assert "non-crisis city" in by_abstract[0]["text"]
        assert by_abstract[0]["run_id"] == run_id
        assert by_abstract[0]["tag"] is not None
        assert by_abstract[0]["paper_id"] is not None

    def test_follow_up_questions_are_found_with_their_thread(self):
        ask = make_ask_team()
        asked = ask.ask("How fast do prices rise after a distribution?")
        assert asked.status_code == 202, asked.text

        [hit] = hits(found(ask.rt.viewer, "fast distribution", "questions"))

        assert hit["thread_id"] == asked.json()["thread_id"]
        assert hit["question_id"] == asked.json()["id"]
        assert hit["review_id"] == ask.rt.review_id

    def test_a_scope_limits_the_search_to_it(self):
        rt = make_run_team()
        write_brief(rt)

        everything = found(rt.viewer, "cash")
        briefs_only = found(rt.viewer, "cash", "briefs")

        assert {h["scope"] for h in hits(everything)} > {"briefs"}
        assert {h["scope"] for h in hits(briefs_only)} == {"briefs"}
        assert briefs_only["scope"] == "briefs"


class TestPrivacy:
    def test_raw_document_text_is_never_searched(self):
        documents = make_document_team()
        documents.ready(b"Field notes: the Zorblax committee met Amina at the clinic.")

        assert found(documents.owner, "Zorblax")["groups"] == []

    def test_search_writes_nothing_to_the_audit_log(self, team):
        before = len(audit_events(team.harness))

        found(team.owner, "cholera")

        assert len(audit_events(team.harness)) == before


class TestLimits:
    @pytest.mark.parametrize("q", ["", "a", "x" * (MAX_QUERY_LENGTH + 1)])
    def test_a_query_too_short_or_too_long_is_refused(self, team, q):
        response = search(team.owner, q)

        assert response.status_code == 422

    def test_an_unknown_scope_is_refused(self, team):
        assert search(team.owner, "cholera", "documents").status_code == 422

    def test_too_many_searches_in_a_minute_are_refused_until_the_window_passes(self, team):
        for _ in range(SEARCHES_PER_MINUTE):
            assert search(team.owner, "cholera").status_code == 200

        refused = search(team.owner, "cholera")

        assert refused.status_code == 429
        assert int(refused.headers["Retry-After"]) >= 1
        assert search(team.viewer, "cholera").status_code == 200
