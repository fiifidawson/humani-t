"""Requirements for collections, tags, pins and the All projects list (UI_UX_SPEC §7.1-7.3).

Collections are labels shared across the deployment with no access rules of their own
(ADR 0016): any signed-in user may create one, a project owner files their project in at
most one, and nothing about a collection ever reveals a project to a non-member, its
counts included. Tags are a project's own text, sealed under its key; pins are per user.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.domain.encryption import EncryptionContext, KeyDestroyedError
from tests.brief_support import write_brief
from tests.pipeline_support import ALL_STAGES, make_pipeline
from tests.project_support import (
    OWNER_EMAIL,
    PROJECTS,
    REVIEWER_EMAIL,
    VIEWER_EMAIL,
    Team,
    add_member,
    audit_events,
    create_project,
    make_team,
    project_events,
    send,
)
from tests.run_support import create_review, make_run_team, run_until_idle

COLLECTIONS = "/api/v1/collections"
ACTIONS = "/api/v1/project-actions"
MISSING = "00000000-0000-4000-8000-000000000000"
# A collection name can say as much as a project name: it is sealed too.
SAHEL = "Sahel 2026, Gao"
TAGS = ["Mali", "cash transfers"]


@pytest.fixture
def team() -> Team:
    return make_team()


def new_collection(browser: Any, name: str = SAHEL) -> dict[str, Any]:
    response = send(browser, "POST", COLLECTIONS, {"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def collections(browser: Any) -> list[dict[str, Any]]:
    response = browser.get(COLLECTIONS)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def by_name(browser: Any, name: str = SAHEL) -> dict[str, Any]:
    return next(c for c in collections(browser) if c["name"] == name)


def move(browser: Any, project_id: str, collection_id: str | None) -> Any:
    path = f"{PROJECTS}/{project_id}/collection"
    return send(browser, "PUT", path, {"collection_id": collection_id})


def set_tags(browser: Any, project_id: str, tags: list[str]) -> Any:
    return send(browser, "PUT", f"{PROJECTS}/{project_id}/tags", {"tags": tags})


def listed(browser: Any, **params: Any) -> list[dict[str, Any]]:
    response = browser.get(PROJECTS, params=params)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def ids(projects: list[dict[str, Any]]) -> list[str]:
    return [p["id"] for p in projects]


def bulk(browser: Any, body: dict[str, Any]) -> Any:
    return send(browser, "POST", ACTIONS, body)


def decrypt(team: Team, context: EncryptionContext, sealed: bytes) -> bytes:
    async def run() -> bytes:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await team.harness.providers.encryption.decrypt(uow, context, sealed)

    return asyncio.run(run())


def stored_tags(team: Team, project_id: str) -> Any:
    async def read() -> Any:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await uow.project_tags.get(UUID(project_id))

    return asyncio.run(read())


def stored_collection(team: Team, collection_id: str) -> Any:
    async def read() -> Any:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await uow.collections.get(UUID(collection_id))

    return asyncio.run(read())


class TestCreatingCollections:
    def test_any_signed_in_user_creates_one_and_may_manage_it(self, team):
        response = send(team.outsider, "POST", COLLECTIONS, {"name": f"  {SAHEL} "})

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == SAHEL
        assert body["project_count"] == 0
        assert body["can_manage"] is True
        assert set(body) == {"id", "name", "project_count", "can_manage", "created_at"}

    def test_every_signed_in_user_sees_every_collection_name(self, team):
        created = new_collection(team.outsider)

        seen = collections(team.viewer)

        assert [(c["id"], c["name"], c["can_manage"]) for c in seen] == [
            (created["id"], SAHEL, False)
        ]

    def test_collections_are_listed_by_name(self, team):
        for name in ("Protection", "health data", "Sahel 2026"):
            new_collection(team.owner, name)

        assert [c["name"] for c in collections(team.owner)] == [
            "health data",
            "Protection",
            "Sahel 2026",
        ]

    def test_a_name_already_used_in_any_spelling_is_a_conflict(self, team):
        new_collection(team.outsider, "Sahel 2026")

        response = send(team.owner, "POST", COLLECTIONS, {"name": "sahel  2026"})

        assert response.status_code == 409
        assert response.json()["collection_name_taken"] is True
        assert len(collections(team.owner)) == 1

    @pytest.mark.parametrize("name", ["", "   ", "Sahel\n2026", "x" * 81])
    def test_an_unusable_name_is_refused(self, team, name):
        response = send(team.owner, "POST", COLLECTIONS, {"name": name})

        assert response.status_code == 422
        assert collections(team.owner) == []

    def test_unknown_fields_are_refused(self, team):
        body = {"name": SAHEL, "created_by": team.user_id(VIEWER_EMAIL)}

        assert send(team.owner, "POST", COLLECTIONS, body).status_code == 422

    def test_the_name_is_stored_only_sealed_under_the_collections_own_key(self, team):
        created = new_collection(team.owner)

        stored = stored_collection(team, created["id"])

        assert SAHEL.encode() not in repr(vars(team.harness.storage.state)).encode()
        context = EncryptionContext(UUID(created["id"]), "collections.name", created["id"])
        assert decrypt(team, context, stored.name_sealed) == SAHEL.encode()

    def test_creating_one_is_audited_with_ids_only(self, team):
        created = new_collection(team.owner)

        events = [
            e for e in audit_events(team.harness) if e.entry.action == "collection.created"
        ]

        assert [e.entry.target_id for e in events] == [created["id"]]
        assert "Sahel" not in repr(events)


class TestCounts:
    def test_a_collection_counts_only_the_callers_own_projects(self, team):
        sahel = new_collection(team.outsider)
        theirs = create_project(team.outsider, "Market assessment, Mopti")
        assert move(team.outsider, theirs["id"], sahel["id"]).status_code == 200
        assert move(team.owner, team.project_id, sahel["id"]).status_code == 200

        counts = {
            who: by_name(browser)["project_count"]
            for who, browser in [
                ("owner", team.owner),
                ("viewer", team.viewer),
                ("outsider", team.outsider),
                ("admin", team.admin),
            ]
        }

        assert counts == {"owner": 1, "viewer": 1, "outsider": 1, "admin": 0}

    def test_archived_projects_are_not_counted(self, team):
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])

        send(team.owner, "POST", f"{PROJECTS}/{team.project_id}/archive")

        assert by_name(team.owner)["project_count"] == 0


class TestRenamingAndDeleting:
    def test_its_creator_renames_it(self, team):
        created = new_collection(team.reviewer)

        response = send(
            team.reviewer, "PATCH", f"{COLLECTIONS}/{created['id']}", {"name": "Sahel"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Sahel"
        assert by_name(team.owner, "Sahel")["id"] == created["id"]

    def test_an_administrator_renames_anyones(self, team):
        created = new_collection(team.reviewer)

        response = send(
            team.admin, "PATCH", f"{COLLECTIONS}/{created['id']}", {"name": "Sahel"}
        )

        assert response.status_code == 200

    def test_nobody_else_may_rename_it(self, team):
        created = new_collection(team.reviewer)

        response = send(team.owner, "PATCH", f"{COLLECTIONS}/{created['id']}", {"name": "Mine"})

        assert response.status_code == 403
        assert by_name(team.owner)["id"] == created["id"]

    def test_renaming_to_a_name_in_use_is_a_conflict(self, team):
        new_collection(team.owner, "Protection")
        created = new_collection(team.owner)

        path = f"{COLLECTIONS}/{created['id']}"
        response = send(team.owner, "PATCH", path, {"name": "PROTECTION"})

        assert response.status_code == 409

    def test_renaming_to_its_own_name_in_another_case_is_allowed(self, team):
        created = new_collection(team.owner, "sahel")

        path = f"{COLLECTIONS}/{created['id']}"
        response = send(team.owner, "PATCH", path, {"name": "Sahel"})

        assert response.status_code == 200
        assert response.json()["name"] == "Sahel"

    def test_a_missing_collection_is_404(self, team):
        assert (
            send(team.owner, "PATCH", f"{COLLECTIONS}/{MISSING}", {"name": "x"}).status_code
            == 404
        )
        assert send(team.owner, "DELETE", f"{COLLECTIONS}/{MISSING}").status_code == 404

    def test_deleting_it_leaves_every_project_it_held_in_no_collection(self, team):
        created = new_collection(team.reviewer)
        theirs = create_project(team.outsider, "Market assessment, Mopti")
        move(team.outsider, theirs["id"], created["id"])
        move(team.owner, team.project_id, created["id"])

        response = send(team.reviewer, "DELETE", f"{COLLECTIONS}/{created['id']}")

        assert response.status_code == 204
        assert collections(team.owner) == []
        assert team.owner.get(f"{PROJECTS}/{team.project_id}").json()["collection_id"] is None
        assert team.outsider.get(f"{PROJECTS}/{theirs['id']}").json()["collection_id"] is None

    def test_nobody_but_its_creator_or_an_administrator_may_delete_it(self, team):
        created = new_collection(team.reviewer)

        assert send(team.owner, "DELETE", f"{COLLECTIONS}/{created['id']}").status_code == 403
        assert send(team.admin, "DELETE", f"{COLLECTIONS}/{created['id']}").status_code == 204

    def test_deleting_it_destroys_its_key_so_its_name_is_unreadable(self, team):
        created = new_collection(team.owner)
        sealed = stored_collection(team, created["id"]).name_sealed

        send(team.owner, "DELETE", f"{COLLECTIONS}/{created['id']}")

        context = EncryptionContext(UUID(created["id"]), "collections.name", created["id"])
        with pytest.raises(KeyDestroyedError):
            decrypt(team, context, sealed)

    def test_renaming_and_deleting_are_audited_with_ids_only(self, team):
        created = new_collection(team.owner)
        send(team.owner, "PATCH", f"{COLLECTIONS}/{created['id']}", {"name": "Sahel"})
        send(team.owner, "DELETE", f"{COLLECTIONS}/{created['id']}")

        actions = [
            e.entry.action
            for e in audit_events(team.harness)
            if e.entry.target_id == created["id"]
        ]

        assert actions == ["collection.created", "collection.renamed", "collection.deleted"]


class TestMovingAProject:
    def test_an_owner_files_their_project_in_a_collection(self, team):
        sahel = new_collection(team.outsider)

        response = move(team.owner, team.project_id, sahel["id"])

        assert response.status_code == 200
        assert response.json()["collection_id"] == sahel["id"]
        assert (
            team.viewer.get(f"{PROJECTS}/{team.project_id}").json()["collection_id"]
            == (sahel["id"])
        )

    def test_a_project_is_in_at_most_one_collection(self, team):
        first, second = new_collection(team.owner, "A"), new_collection(team.owner, "B")
        move(team.owner, team.project_id, first["id"])

        move(team.owner, team.project_id, second["id"])

        assert by_name(team.owner, "A")["project_count"] == 0
        assert by_name(team.owner, "B")["project_count"] == 1

    def test_moving_to_no_collection_takes_it_out(self, team):
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])

        response = move(team.owner, team.project_id, None)

        assert response.json()["collection_id"] is None
        assert by_name(team.owner)["project_count"] == 0

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_only_owners_may_move_it(self, team, role):
        sahel = new_collection(team.owner)

        response = move(team.browser_for(role), team.project_id, sahel["id"])

        assert response.status_code == 403
        assert by_name(team.owner)["project_count"] == 0

    def test_a_non_member_gets_404(self, team):
        sahel = new_collection(team.outsider)

        assert move(team.outsider, team.project_id, sahel["id"]).status_code == 404

    def test_an_unknown_collection_is_refused_and_nothing_moves(self, team):
        response = move(team.owner, team.project_id, MISSING)

        assert response.status_code == 422
        assert team.owner.get(f"{PROJECTS}/{team.project_id}").json()["collection_id"] is None

    def test_a_move_is_audited_with_ids_only(self, team):
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])

        events = project_events(team.harness, team.project_id)

        assert "project.collection_changed" in [e.entry.action for e in events]
        assert "Sahel" not in repr(events)


class TestTags:
    def test_an_owner_sets_the_projects_tags(self, team):
        response = set_tags(team.owner, team.project_id, [" Mali", "cash   transfers", "mali"])

        assert response.status_code == 200
        assert response.json()["tags"] == TAGS

    def test_setting_tags_replaces_the_old_ones(self, team):
        set_tags(team.owner, team.project_id, TAGS)

        response = set_tags(team.owner, team.project_id, ["protection"])

        assert response.json()["tags"] == ["protection"]

    def test_every_member_sees_them(self, team):
        set_tags(team.owner, team.project_id, TAGS)

        assert team.viewer.get(f"{PROJECTS}/{team.project_id}").json()["tags"] == TAGS
        assert listed(team.reviewer)[0]["tags"] == TAGS

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_only_owners_may_set_them(self, team, role):
        response = set_tags(team.browser_for(role), team.project_id, TAGS)

        assert response.status_code == 403
        assert stored_tags(team, team.project_id) is None

    def test_a_non_member_gets_404(self, team):
        assert set_tags(team.outsider, team.project_id, TAGS).status_code == 404

    @pytest.mark.parametrize("tags", [["  "], ["x" * 41], [f"t{n}" for n in range(21)]])
    def test_unusable_tags_are_refused(self, team, tags):
        response = set_tags(team.owner, team.project_id, tags)

        assert response.status_code == 422
        assert stored_tags(team, team.project_id) is None

    def test_tags_are_stored_only_sealed_under_the_projects_key(self, team):
        set_tags(team.owner, team.project_id, TAGS)

        stored = stored_tags(team, team.project_id)

        assert b"cash transfers" not in repr(vars(team.harness.storage.state)).encode()
        context = EncryptionContext(UUID(team.project_id), "project_tags.tags", team.project_id)
        assert b"cash transfers" in decrypt(team, context, stored.tags_sealed)

    def test_deleting_the_project_makes_its_tags_unreadable(self, team):
        set_tags(team.owner, team.project_id, TAGS)
        sealed = stored_tags(team, team.project_id).tags_sealed

        send(team.owner, "DELETE", f"{PROJECTS}/{team.project_id}")

        context = EncryptionContext(UUID(team.project_id), "project_tags.tags", team.project_id)
        with pytest.raises(KeyDestroyedError):
            decrypt(team, context, sealed)

    def test_a_change_is_audited_without_the_tags(self, team):
        set_tags(team.owner, team.project_id, TAGS)

        events = project_events(team.harness, team.project_id)

        assert "project.tags_changed" in [e.entry.action for e in events]
        assert "Mali" not in repr(events)


class TestPins:
    def test_any_member_pins_a_project_for_themselves_only(self, team):
        response = send(team.viewer, "PUT", f"{PROJECTS}/{team.project_id}/pin")

        assert response.status_code == 204
        assert team.viewer.get(f"{PROJECTS}/{team.project_id}").json()["is_pinned"] is True
        assert team.owner.get(f"{PROJECTS}/{team.project_id}").json()["is_pinned"] is False
        assert listed(team.viewer)[0]["pinned_at"] is not None
        assert listed(team.owner)[0]["pinned_at"] is None

    def test_pinning_twice_changes_nothing(self, team):
        send(team.viewer, "PUT", f"{PROJECTS}/{team.project_id}/pin")
        first = listed(team.viewer)[0]["pinned_at"]
        team.harness.clock.advance(minutes=5)

        assert send(team.viewer, "PUT", f"{PROJECTS}/{team.project_id}/pin").status_code == 204
        assert listed(team.viewer)[0]["pinned_at"] == first

    def test_unpinning_removes_the_pin(self, team):
        send(team.viewer, "PUT", f"{PROJECTS}/{team.project_id}/pin")

        response = send(team.viewer, "DELETE", f"{PROJECTS}/{team.project_id}/pin")

        assert response.status_code == 204
        assert team.viewer.get(f"{PROJECTS}/{team.project_id}").json()["is_pinned"] is False

    def test_a_non_member_cannot_pin(self, team):
        assert (
            send(team.outsider, "PUT", f"{PROJECTS}/{team.project_id}/pin").status_code == 404
        )

    def test_leaving_a_project_drops_the_pin(self, team):
        send(team.viewer, "PUT", f"{PROJECTS}/{team.project_id}/pin")
        viewer_id = team.user_id(VIEWER_EMAIL)
        send(team.viewer, "DELETE", f"{PROJECTS}/{team.project_id}/members/{viewer_id}")

        add_member(team.owner, team.project_id, VIEWER_EMAIL, "viewer")

        assert team.viewer.get(f"{PROJECTS}/{team.project_id}").json()["is_pinned"] is False


class TestTheList:
    def test_each_project_carries_what_grouping_needs(self, team):
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])
        set_tags(team.owner, team.project_id, TAGS)

        (project,) = listed(team.viewer)

        assert project["collection_id"] == sahel["id"]
        assert project["tags"] == TAGS
        assert project["status"] == "planning"
        assert project["last_activity_at"] >= project["created_at"]
        assert {(m["user_id"], m["role"]) for m in project["members"]} == {
            (team.user_id(OWNER_EMAIL), "owner"),
            (team.user_id(REVIEWER_EMAIL), "reviewer"),
            (team.user_id(VIEWER_EMAIL), "viewer"),
        }
        assert project["relevant_papers"] is None
        assert project["papers_found"] is None
        assert project["brief_version"] is None

    def test_a_list_never_names_a_project_the_caller_is_not_in(self, team):
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])
        set_tags(team.owner, team.project_id, TAGS)

        for params in (
            {},
            {"collection": sahel["id"]},
            {"tag": "Mali"},
            {"include_archived": True},
        ):
            assert listed(team.outsider, **params) == []

    def test_last_activity_is_the_default_order(self, team):
        older = create_project(team.owner, "Biometric registration")
        team.harness.clock.advance(minutes=5)
        create_review(team.owner, older["id"], "Does biometric registration cut duplicate aid?")

        assert ids(listed(team.owner)) == [older["id"], team.project_id]

    def test_sorting_by_name(self, team):
        other = create_project(team.owner, "Biometric registration")

        assert ids(listed(team.owner, sort="name")) == [other["id"], team.project_id]

    def test_sorting_by_created_is_newest_first(self, team):
        team.harness.clock.advance(minutes=1)
        newer = create_project(team.owner, "Biometric registration")

        assert ids(listed(team.owner, sort="created")) == [newer["id"], team.project_id]

    def test_an_unknown_sort_is_refused(self, team):
        assert team.owner.get(PROJECTS, params={"sort": "size"}).status_code == 422

    def test_filtering_by_tags_needs_every_tag(self, team):
        other = create_project(team.owner, "Biometric registration")
        set_tags(team.owner, team.project_id, TAGS)
        set_tags(team.owner, other["id"], ["Mali"])

        assert set(ids(listed(team.owner, tag=["mali"]))) == {other["id"], team.project_id}
        assert ids(listed(team.owner, tag=["Mali", "cash transfers"])) == [team.project_id]

    def test_filtering_by_status(self, team):
        assert ids(listed(team.owner, status=["planning"])) == [team.project_id]
        assert listed(team.owner, status=["failed", "running"]) == []

    def test_the_archived_status_brings_archived_projects_back(self, team):
        send(team.owner, "POST", f"{PROJECTS}/{team.project_id}/archive")

        assert listed(team.owner) == []
        assert ids(listed(team.owner, status=["archived"])) == [team.project_id]
        assert ids(listed(team.owner, include_archived=True)) == [team.project_id]

    def test_filtering_by_owner_and_by_member(self, team):
        theirs = create_project(team.outsider, "Market assessment, Mopti")
        add_member(team.outsider, theirs["id"], VIEWER_EMAIL, "viewer")

        viewer_id, amara_id = team.user_id(VIEWER_EMAIL), team.user_id(OWNER_EMAIL)

        assert ids(listed(team.viewer, owner=amara_id)) == [team.project_id]
        assert ids(listed(team.viewer, member=team.user_id(REVIEWER_EMAIL))) == [
            team.project_id
        ]
        assert len(listed(team.viewer, member=viewer_id)) == 2

    def test_filtering_by_created_date(self, team):
        created = listed(team.owner)[0]["created_at"][:10]

        assert ids(listed(team.owner, created_from=created, created_to=created)) == [
            team.project_id
        ]
        assert listed(team.owner, created_from="2099-01-01") == []

    def test_a_date_range_that_ends_before_it_starts_is_refused(self, team):
        params = {"created_from": "2026-11-10", "created_to": "2026-11-01"}

        assert team.owner.get(PROJECTS, params=params).status_code == 422

    def test_filtering_by_collection_and_by_no_collection(self, team):
        other = create_project(team.owner, "Biometric registration")
        sahel = new_collection(team.owner)
        move(team.owner, team.project_id, sahel["id"])

        assert ids(listed(team.owner, collection=sahel["id"])) == [team.project_id]
        assert ids(listed(team.owner, collection="none")) == [other["id"]]

    def test_an_unreadable_collection_filter_is_refused(self, team):
        assert team.owner.get(PROJECTS, params={"collection": "sahel"}).status_code == 422

    def test_a_screened_project_carries_its_relevant_papers_and_sorts_by_them(self):
        p = make_pipeline()
        p.rt.start_run(ALL_STAGES)
        run_until_idle(p.rt.worker(p.stages()))
        empty = create_project(p.rt.owner, "Biometric registration")

        projects = listed(p.rt.owner, sort="relevant_papers")

        assert ids(projects) == [p.rt.project_id, empty["id"]]
        assert (projects[0]["relevant_papers"], projects[0]["papers_found"]) == (3, 5)

    def test_a_project_with_a_brief_carries_its_version(self):
        rt = make_run_team()
        write_brief(rt)

        assert listed(rt.owner)[0]["brief_version"] == 1


class TestBulkActions:
    def test_an_owner_moves_several_projects_at_once(self, team):
        other = create_project(team.owner, "Biometric registration")
        sahel = new_collection(team.owner)

        response = bulk(
            team.owner,
            {
                "action": "move",
                "project_ids": [team.project_id, other["id"]],
                "collection_id": sahel["id"],
            },
        )

        assert response.status_code == 200
        assert sorted(response.json()["changed"]) == sorted([team.project_id, other["id"]])
        assert response.json()["skipped"] == []
        assert by_name(team.owner)["project_count"] == 2

    def test_a_mixed_selection_changes_only_the_callers_own_projects(self, team):
        theirs = create_project(team.outsider, "Market assessment, Mopti")
        hidden = create_project(team.outsider, "Staff security incident")
        add_member(team.outsider, theirs["id"], OWNER_EMAIL, "viewer")

        response = bulk(
            team.owner,
            {"action": "archive", "project_ids": [team.project_id, theirs["id"], hidden["id"]]},
        )

        assert response.status_code == 200
        assert response.json()["changed"] == [team.project_id]
        assert response.json()["skipped"] == [
            {"project_id": theirs["id"], "reason": "not_allowed"},
            {"project_id": hidden["id"], "reason": "not_found"},
        ]
        assert team.outsider.get(f"{PROJECTS}/{theirs['id']}").json()["archived_at"] is None

    def test_a_project_the_caller_is_not_in_looks_exactly_like_a_missing_one(self, team):
        hidden = create_project(team.outsider, "Staff security incident")

        response = bulk(
            team.owner, {"action": "archive", "project_ids": [hidden["id"], MISSING]}
        )

        assert response.json()["skipped"] == [
            {"project_id": hidden["id"], "reason": "not_found"},
            {"project_id": MISSING, "reason": "not_found"},
        ]

    def test_adding_a_tag_keeps_the_tags_already_there(self, team):
        other = create_project(team.owner, "Biometric registration")
        set_tags(team.owner, team.project_id, TAGS)

        response = bulk(
            team.owner,
            {"action": "add_tag", "project_ids": [team.project_id, other["id"]], "tag": "MALI"},
        )

        assert response.status_code == 200
        tags = {p["id"]: p["tags"] for p in listed(team.owner)}
        assert tags == {team.project_id: TAGS, other["id"]: ["MALI"]}

    def test_an_unknown_collection_refuses_the_whole_request(self, team):
        response = bulk(
            team.owner,
            {"action": "move", "project_ids": [team.project_id], "collection_id": MISSING},
        )

        assert response.status_code == 422
        assert listed(team.owner)[0]["collection_id"] is None

    @pytest.mark.parametrize(
        "body",
        [
            {"action": "archive", "project_ids": []},
            {"action": "archive", "project_ids": [MISSING] * 201},
            {"action": "delete", "project_ids": [MISSING]},
            {"action": "add_tag", "project_ids": [MISSING], "tag": " "},
            {"action": "move", "project_ids": [MISSING]},
        ],
    )
    def test_a_malformed_request_is_refused(self, team, body):
        assert bulk(team.owner, body).status_code == 422
