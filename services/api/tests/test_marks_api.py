"""Requirements for seen and saved marks over HTTP (UI_UX_SPEC §7.5).

A paper the person opened is seen wherever the same paper appears in a project they can
access, matched as the dedup rule matches papers: DOI first, else title and year. Marks are
per person, and never say anything about a project the person is not a member of.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.domain.encryption import EncryptionContext, KeyDestroyedError
from tests.history_support import DOI, TITLE, add_paper, second_project
from tests.project_support import (
    OWNER_EMAIL,
    PROJECT_NAME,
    REVIEWER_EMAIL,
    add_member,
    send,
)
from tests.run_support import RunTeam, make_run_team

SAVED = "/api/v1/saved-papers"
IDENTITY_PURPOSE = "paper_marks.identity"


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def paper_path(project_id: str, paper_id: str, mark: str) -> str:
    return f"/api/v1/projects/{project_id}/papers/{paper_id}/{mark}"


def see(browser: Any, project_id: str, paper_id: str) -> Any:
    return send(browser, "PUT", paper_path(project_id, paper_id, "seen"))


def save(browser: Any, project_id: str, paper_id: str) -> Any:
    return send(browser, "PUT", paper_path(project_id, paper_id, "saved"))


def marks(browser: Any, project_id: str) -> dict[str, dict[str, Any]]:
    response = browser.get(f"/api/v1/projects/{project_id}/paper-marks")
    assert response.status_code == 200, response.text
    return {m["paper_id"]: m for m in response.json()["papers"]}


def stored_marks(rt: RunTeam) -> list[Any]:
    async def read() -> list[Any]:
        async with rt.harness.providers.storage.unit_of_work() as uow:
            return await uow.marks.list_paper_marks(UUID(rt.team.user_id(OWNER_EMAIL)))

    return asyncio.run(read())


def open_identity(rt: RunTeam, mark: Any) -> bytes:
    context = EncryptionContext(
        mark.project_id, IDENTITY_PURPOSE, f"{mark.user_id}:{mark.paper_id}:{mark.kind.value}"
    )

    async def read() -> bytes:
        async with rt.harness.providers.storage.unit_of_work() as uow:
            return await rt.harness.providers.encryption.decrypt(
                uow, context, mark.identity_sealed
            )

    return asyncio.run(read())


class TestSeen:
    def test_opening_a_paper_marks_it_seen_for_that_person_only(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)

        assert see(rt.reviewer, rt.project_id, paper).status_code == 204

        assert marks(rt.reviewer, rt.project_id)[paper] == {
            "paper_id": paper,
            "is_seen": True,
            "is_saved": False,
            "seen_in_other_projects": [],
        }
        assert marks(rt.owner, rt.project_id) == {}

    def test_marking_again_changes_nothing(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)
        see(rt.owner, rt.project_id, paper)
        first = stored_marks(rt)

        rt.clock.advance(minutes=5)
        see(rt.owner, rt.project_id, paper)

        assert stored_marks(rt) == first

    def test_the_same_doi_in_another_project_is_seen_and_names_where(self, rt):
        here = add_paper(rt.harness, rt.project_id, rt.review_id)
        other_project, other_review = second_project(rt, "Market assessment, Mopti")
        there = add_paper(
            rt.harness, other_project, other_review, doi=f"https://doi.org/{DOI.upper()}"
        )

        see(rt.owner, rt.project_id, here)

        assert marks(rt.owner, other_project)[there]["is_seen"] is True
        assert marks(rt.owner, other_project)[there]["seen_in_other_projects"] == [
            {"id": rt.project_id, "name": PROJECT_NAME}
        ]

    def test_without_a_doi_the_title_and_year_match_and_a_different_year_does_not(self, rt):
        here = add_paper(rt.harness, rt.project_id, rt.review_id, doi=None)
        other_project, other_review = second_project(rt, "Market assessment, Mopti")
        same = add_paper(rt.harness, other_project, other_review, doi=None, title=TITLE.upper())
        preprint = add_paper(rt.harness, other_project, other_review, doi=None, year=2025)

        see(rt.owner, rt.project_id, here)

        found = marks(rt.owner, other_project)
        assert same in found
        assert preprint not in found

    def test_a_project_the_person_is_not_a_member_of_is_never_named(self, rt):
        here = add_paper(rt.harness, rt.project_id, rt.review_id)
        other_project, other_review = second_project(rt, "Market assessment, Mopti")
        there = add_paper(rt.harness, other_project, other_review)
        add_member(rt.owner, other_project, REVIEWER_EMAIL, "reviewer")
        see(rt.reviewer, rt.project_id, here)
        assert marks(rt.reviewer, other_project)[there]["seen_in_other_projects"] == [
            {"id": rt.project_id, "name": PROJECT_NAME}
        ]
        reviewer_id = rt.team.user_id(REVIEWER_EMAIL)

        send(rt.owner, "DELETE", f"/api/v1/projects/{rt.project_id}/members/{reviewer_id}")

        assert marks(rt.reviewer, other_project) == {}

    def test_a_paper_from_another_project_cannot_be_marked_through_this_one(self, rt):
        other_project, other_review = second_project(rt, "Market assessment, Mopti")
        there = add_paper(rt.harness, other_project, other_review)

        assert see(rt.owner, rt.project_id, there).status_code == 404

    def test_a_non_member_learns_nothing(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)
        see(rt.owner, rt.project_id, paper)

        assert see(rt.outsider, rt.project_id, paper).status_code == 404
        assert (
            rt.outsider.get(f"/api/v1/projects/{rt.project_id}/paper-marks").status_code == 404
        )

    def test_the_identity_is_sealed_under_the_project_key_and_dies_with_it(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)
        see(rt.owner, rt.project_id, paper)
        (mark,) = stored_marks(rt)
        assert DOI.encode() not in mark.identity_sealed
        assert open_identity(rt, mark) == f"doi:{DOI}".encode()

        send(rt.owner, "DELETE", f"/api/v1/projects/{rt.project_id}")

        with pytest.raises(KeyDestroyedError):
            open_identity(rt, mark)


class TestSaved:
    def test_saving_adds_the_paper_to_the_saved_list(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)

        assert save(rt.owner, rt.project_id, paper).status_code == 204

        (saved,) = rt.owner.get(SAVED).json()["papers"]
        assert saved["paper_id"] == paper
        assert saved["project"] == {"id": rt.project_id, "name": PROJECT_NAME}
        assert saved["review_id"] == rt.review_id
        assert saved["title"] == TITLE
        assert saved["doi"] == DOI
        assert marks(rt.owner, rt.project_id)[paper]["is_saved"] is True
        assert rt.reviewer.get(SAVED).json() == {"papers": []}

    def test_unsaving_takes_it_off_the_list(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)
        save(rt.owner, rt.project_id, paper)

        response = send(rt.owner, "DELETE", paper_path(rt.project_id, paper, "saved"))

        assert response.status_code == 204
        assert rt.owner.get(SAVED).json() == {"papers": []}

    def test_a_saved_paper_leaves_the_list_with_the_project_it_came_from(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)
        save(rt.reviewer, rt.project_id, paper)
        reviewer_id = rt.team.user_id(REVIEWER_EMAIL)

        send(rt.owner, "DELETE", f"/api/v1/projects/{rt.project_id}/members/{reviewer_id}")

        assert rt.reviewer.get(SAVED).json() == {"papers": []}

    def test_a_viewer_may_save_for_themselves(self, rt):
        paper = add_paper(rt.harness, rt.project_id, rt.review_id)

        assert save(rt.viewer, rt.project_id, paper).status_code == 204


class TestBriefSeen:
    def test_a_non_member_cannot_mark_a_brief(self, rt):
        from tests.brief_support import stored_brief, write_brief

        brief = stored_brief(rt, write_brief(rt))
        assert brief is not None

        response = send(rt.outsider, "PUT", f"/api/v1/briefs/{brief.id}/seen")

        assert response.status_code == 404
