"""Requirements for projects, membership and roles, over HTTP.

Rules apply in order: 401 when not signed in; 404 for a non-member or a missing project,
with the same body, so a project's existence is never confirmed; 403 for a role that may
not; 401 `reauthentication_required` for a sensitive action after 15 minutes; then 422 or
409. A password sign-in is a full sign-in here; MFA is specified separately.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from app.domain.encryption import KeyDestroyedError
from tests.project_support import (
    OUTSIDER_EMAIL,
    OWNER_EMAIL,
    PASSWORD,
    PROJECT_NAME,
    PROJECTS,
    REVIEWER_EMAIL,
    VIEWER_EMAIL,
    Team,
    add_member,
    create_project,
    decrypt_name,
    fill_path,
    make_project_harness,
    make_team,
    project_events,
    send,
    signed_in,
    stored_memberships,
    stored_name_ciphertext,
    verify_audit,
)
from tests.support import AuthHarness, csrf_headers

VOCABULARY = {
    "origin": "llm_assisted",
    "concepts": [
        {"term": "chatbot", "synonyms": ["conversational agent"], "theme": "technology"},
        {"term": "humanitarian", "synonyms": ["crisis response"], "theme": "setting"},
    ],
}

PROJECT_FIELDS = {"id", "name", "role", "created_at", "archived_at"}
MEMBER_FIELDS = {"user_id", "display_name", "role", "added_at"}


@pytest.fixture
def team() -> Team:
    return make_team()


@pytest.fixture
def harness() -> AuthHarness:
    """Users who belong to no project: for the endpoints that need none."""
    harness = make_project_harness()
    harness.create_user(OUTSIDER_EMAIL, PASSWORD)
    return harness


def _reauthenticate(browser) -> None:
    response = send(browser, "POST", "/api/v1/auth/reauthenticate", {"password": PASSWORD})
    assert response.status_code == 204


def _stale_sign_in(team: Team) -> None:
    """Move past the 15-minute recent-sign-in window, keeping every session alive."""
    team.harness.clock.advance(minutes=16)


class TestCreateProject:
    def test_a_signed_in_user_creates_a_project_and_becomes_its_owner(self, team):
        response = send(team.outsider, "POST", PROJECTS, {"name": "Market assessment, Mopti"})

        assert response.status_code == 201
        body = response.json()
        assert set(body) >= PROJECT_FIELDS
        assert body["name"] == "Market assessment, Mopti"
        assert body["role"] == "owner"
        assert body["archived_at"] is None
        assert response.headers["location"] == f"{PROJECTS}/{body['id']}"

    def test_the_creator_is_listed_as_the_only_member(self, team):
        project = create_project(team.outsider, "Market assessment, Mopti")

        members = team.outsider.get(f"{PROJECTS}/{project['id']}/members").json()

        assert [(m["user_id"], m["role"]) for m in members] == [
            (team.user_id(OUTSIDER_EMAIL), "owner")
        ]

    def test_each_project_gets_its_own_live_data_key(self, team):
        other = create_project(team.owner, "Market assessment, Mopti")
        keys = team.harness.storage.state.data_keys

        for project_id in (team.project_id, other["id"]):
            assert UUID(project_id) in keys
            assert not keys[UUID(project_id)].is_destroyed

    def test_the_name_is_never_stored_in_the_clear(self, team):
        """Brief rule 15: project content is encrypted at rest under the project's key."""
        everything_stored = repr(vars(team.harness.storage.state))

        assert "Cholera" not in everything_stored

    def test_the_stored_name_decrypts_only_with_the_projects_own_key(self, team):
        sealed = stored_name_ciphertext(team.harness, team.project_id)

        assert decrypt_name(team.harness, team.project_id, sealed) == PROJECT_NAME.encode()

    def test_surrounding_whitespace_is_trimmed_from_the_name(self, team):
        body = create_project(team.owner, "  Cash vs vouchers, Gao  ")

        assert body["name"] == "Cash vs vouchers, Gao"

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201, "Gao\nresponse"])
    def test_an_unusable_name_is_refused(self, team, name):
        response = send(team.owner, "POST", PROJECTS, {"name": name})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    def test_fields_the_client_may_not_set_are_refused(self, team):
        """No mass assignment: the owner, id and dates are the server's to decide."""
        response = send(
            team.owner, "POST", PROJECTS, {"name": "Gao", "id": str(uuid.uuid4()), "role": "x"}
        )

        assert response.status_code == 422

    def test_creating_needs_the_csrf_token(self, team):
        response = team.owner.post(PROJECTS, json={"name": "Gao"})

        assert response.status_code == 403


class TestListProjects:
    def test_a_user_sees_only_the_projects_they_belong_to(self, team):
        mopti = create_project(team.outsider, "Market assessment, Mopti")

        owners_view = {p["id"] for p in team.owner.get(PROJECTS).json()}
        outsiders_view = {p["id"] for p in team.outsider.get(PROJECTS).json()}

        assert owners_view == {team.project_id}
        assert outsiders_view == {mopti["id"]}

    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_each_listed_project_carries_the_callers_own_role(self, team, role):
        (project,) = team.browser_for(role).get(PROJECTS).json()

        assert project["role"] == role
        assert project["name"] == PROJECT_NAME

    def test_a_user_with_no_projects_gets_an_empty_list(self, team):
        response = team.outsider.get(PROJECTS)

        assert response.status_code == 200
        assert response.json() == []

    def test_an_instance_administrator_sees_only_projects_they_belong_to(self, team):
        """Administering the deployment grants nothing inside a project."""
        assert team.admin.get(PROJECTS).json() == []

    def test_archived_projects_are_hidden_unless_asked_for(self, team):
        send(team.owner, "POST", team.path("/archive"))

        default = team.owner.get(PROJECTS).json()
        with_archived = team.owner.get(PROJECTS, params={"include_archived": "true"}).json()

        assert default == []
        assert [p["id"] for p in with_archived] == [team.project_id]


class TestReadProject:
    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_can_open_the_project(self, team, role):
        response = team.browser_for(role).get(team.path())

        assert response.status_code == 200
        assert response.json()["id"] == team.project_id
        assert response.json()["name"] == PROJECT_NAME
        assert response.json()["role"] == role

    def test_a_non_member_gets_404(self, team):
        response = team.outsider.get(team.path())

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    def test_an_instance_administrator_who_is_not_a_member_gets_404(self, team):
        assert team.admin.get(team.path()).status_code == 404

    def test_a_non_member_cannot_tell_an_existing_project_from_a_missing_one(self, team):
        existing = team.outsider.get(team.path())
        missing = team.outsider.get(f"{PROJECTS}/{uuid.uuid4()}")

        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()

    def test_a_non_member_cannot_tell_them_apart_by_writing_either(self, team):
        """The 404 comes before the role check and before the body is validated."""
        for method, suffix, body in [
            ("PATCH", "", {"name": "Renamed"}),
            ("DELETE", "", None),
            ("POST", "/members", {}),
        ]:
            existing = send(team.outsider, method, team.path(suffix), body)
            missing = send(team.outsider, method, f"{PROJECTS}/{uuid.uuid4()}{suffix}", body)

            assert existing.status_code == missing.status_code == 404, (method, suffix)
            assert existing.json() == missing.json()


class TestRenameProject:
    def test_an_owner_renames_the_project(self, team):
        response = send(team.owner, "PATCH", team.path(), {"name": "Cholera response, Aden"})

        assert response.status_code == 200
        assert response.json()["name"] == "Cholera response, Aden"
        assert team.viewer.get(team.path()).json()["name"] == "Cholera response, Aden"

    def test_the_new_name_is_not_stored_in_the_clear_either(self, team):
        send(team.owner, "PATCH", team.path(), {"name": "Diphtheria response, Aden"})

        assert "Diphtheria" not in repr(vars(team.harness.storage.state))

    def test_the_new_name_follows_the_same_rules(self, team):
        assert send(team.owner, "PATCH", team.path(), {"name": "  "}).status_code == 422


class TestArchiveProject:
    def test_an_owner_archives_and_unarchives_a_project(self, team):
        archived = send(team.owner, "POST", team.path("/archive"))
        unarchived = send(team.owner, "POST", team.path("/unarchive"))

        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
        assert unarchived.status_code == 200
        assert unarchived.json()["archived_at"] is None

    def test_an_archived_project_keeps_everything_and_stays_open_to_its_members(self, team):
        send(team.owner, "POST", team.path("/archive"))

        response = team.viewer.get(team.path())

        assert response.status_code == 200
        assert response.json()["name"] == PROJECT_NAME
        assert response.json()["archived_at"] is not None

    def test_archiving_twice_keeps_the_first_date(self, team):
        first = send(team.owner, "POST", team.path("/archive")).json()["archived_at"]
        team.harness.clock.advance(hours=1)

        second = send(team.owner, "POST", team.path("/archive"))

        assert second.status_code == 200
        assert second.json()["archived_at"] == first


class TestMembers:
    @pytest.mark.parametrize("role", ["owner", "reviewer", "viewer"])
    def test_every_member_can_see_who_else_is_a_member(self, team, role):
        response = team.browser_for(role).get(team.path("/members"))

        assert response.status_code == 200
        assert {(m["user_id"], m["role"]) for m in response.json()} == {
            (team.user_id(OWNER_EMAIL), "owner"),
            (team.user_id(REVIEWER_EMAIL), "reviewer"),
            (team.user_id(VIEWER_EMAIL), "viewer"),
        }

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_the_member_list_shows_others_names_not_email_addresses_or_credentials(
        self, team, role
    ):
        members = team.browser_for(role).get(team.path("/members")).json()

        for member in members:
            assert set(member) == MEMBER_FIELDS
        assert "example.org" not in str(members)

    def test_owners_see_each_members_email_to_tell_people_apart(self, team):
        members = team.owner.get(team.path("/members")).json()

        for member in members:
            assert set(member) == MEMBER_FIELDS | {"email"}
        assert {m["email"] for m in members} == {OWNER_EMAIL, REVIEWER_EMAIL, VIEWER_EMAIL}

    def test_a_member_added_or_changed_by_an_owner_comes_back_with_their_email(self, team):
        added = add_member(team.owner, team.project_id, OUTSIDER_EMAIL, "viewer")
        changed = send(
            team.owner,
            "PATCH",
            team.path(f"/members/{team.user_id(VIEWER_EMAIL)}"),
            {"role": "reviewer"},
        ).json()

        assert added["email"] == OUTSIDER_EMAIL
        assert changed["email"] == VIEWER_EMAIL

    def test_an_owner_adds_an_existing_user_with_a_role(self, team):
        response = send(
            team.owner,
            "POST",
            team.path("/members"),
            {"email": OUTSIDER_EMAIL, "role": "viewer"},
        )

        assert response.status_code == 201
        assert response.json()["user_id"] == team.user_id(OUTSIDER_EMAIL)
        assert response.json()["role"] == "viewer"
        assert team.outsider.get(team.path()).status_code == 200

    def test_the_email_is_matched_case_insensitively(self, team):
        member = add_member(team.owner, team.project_id, OUTSIDER_EMAIL.upper(), "reviewer")

        assert member["user_id"] == team.user_id(OUTSIDER_EMAIL)

    def test_an_owner_may_add_another_owner(self, team):
        assert (
            add_member(team.owner, team.project_id, OUTSIDER_EMAIL, "owner")["role"] == "owner"
        )

    def test_an_email_with_no_account_is_refused_as_bad_input(self, team):
        """422, not 404: on a project route a 404 means "no such project" to the UI."""
        response = send(
            team.owner,
            "POST",
            team.path("/members"),
            {"email": "nobody@example.org", "role": "viewer"},
        )

        assert response.status_code == 422
        assert "nobody@example.org" not in response.text

    def test_a_disabled_account_looks_exactly_like_no_account(self, team):
        send(team.admin, "POST", f"/api/v1/admin/users/{team.user_id(OUTSIDER_EMAIL)}/disable")

        disabled = send(
            team.owner,
            "POST",
            team.path("/members"),
            {"email": OUTSIDER_EMAIL, "role": "viewer"},
        )
        unknown = send(
            team.owner,
            "POST",
            team.path("/members"),
            {"email": "nobody@example.org", "role": "viewer"},
        )

        assert disabled.status_code == unknown.status_code == 422
        assert disabled.json() == unknown.json()

    def test_adding_someone_who_is_already_a_member_is_a_conflict(self, team):
        response = send(
            team.owner, "POST", team.path("/members"), {"email": VIEWER_EMAIL, "role": "owner"}
        )

        assert response.status_code == 409
        assert team.viewer.get(team.path()).json()["role"] == "viewer"

    @pytest.mark.parametrize("role", ["admin", "OWNER", ""])
    def test_only_the_three_project_roles_can_be_given(self, team, role):
        response = send(
            team.owner, "POST", team.path("/members"), {"email": OUTSIDER_EMAIL, "role": role}
        )

        assert response.status_code == 422

    def test_an_owner_changes_a_members_role(self, team):
        viewer_id = team.user_id(VIEWER_EMAIL)

        response = send(
            team.owner, "PATCH", team.path(f"/members/{viewer_id}"), {"role": "reviewer"}
        )

        assert response.status_code == 200
        assert response.json()["role"] == "reviewer"
        assert team.viewer.get(team.path()).json()["role"] == "reviewer"

    def test_an_owner_removes_a_member_who_then_cannot_find_the_project(self, team):
        viewer_id = team.user_id(VIEWER_EMAIL)

        response = send(team.owner, "DELETE", team.path(f"/members/{viewer_id}"))

        assert response.status_code == 204
        assert team.viewer.get(team.path()).status_code == 404
        assert team.viewer.get(PROJECTS).json() == []

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_any_member_can_leave(self, team, role):
        browser = team.browser_for(role)
        own_id = team.user_id({"reviewer": REVIEWER_EMAIL, "viewer": VIEWER_EMAIL}[role])

        response = send(browser, "DELETE", team.path(f"/members/{own_id}"))

        assert response.status_code == 204
        assert browser.get(team.path()).status_code == 404

    def test_leaving_does_not_need_a_recent_sign_in(self, team):
        """It only takes away the caller's own access."""
        _stale_sign_in(team)

        response = send(
            team.viewer, "DELETE", team.path(f"/members/{team.user_id(VIEWER_EMAIL)}")
        )

        assert response.status_code == 204

    @pytest.mark.parametrize("method", ["PATCH", "DELETE"])
    def test_changing_someone_who_is_not_a_member_is_404(self, team, method):
        body = {"role": "viewer"} if method == "PATCH" else None

        response = send(
            team.owner, method, team.path(f"/members/{team.user_id(OUTSIDER_EMAIL)}"), body
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        ("method", "suffix", "body", "expected"),
        [
            ("POST", "/members", {"email": OUTSIDER_EMAIL, "role": "viewer"}, 201),
            ("PATCH", "/members/{viewer_id}", {"role": "reviewer"}, 200),
            ("DELETE", "/members/{viewer_id}", None, 204),
        ],
        ids=["add", "change-role", "remove"],
    )
    def test_member_changes_need_a_sign_in_within_15_minutes(
        self, team, method, suffix, body, expected
    ):
        path = team.path(fill_path(suffix, viewer_id=team.user_id(VIEWER_EMAIL)))
        _stale_sign_in(team)

        refused = send(team.owner, method, path, body)
        _reauthenticate(team.owner)
        accepted = send(team.owner, method, path, body)

        assert refused.status_code == 401
        assert refused.json()["reauthentication_required"] is True
        assert accepted.status_code == expected


class TestLastOwner:
    """The last owner can't leave or be demoted (the UI explains why)."""

    def test_the_last_owner_cannot_be_demoted(self, team):
        owner_id = team.user_id(OWNER_EMAIL)

        response = send(
            team.owner, "PATCH", team.path(f"/members/{owner_id}"), {"role": "viewer"}
        )

        assert response.status_code == 409
        assert response.json()["last_owner"] is True
        assert team.owner.get(team.path()).json()["role"] == "owner"

    def test_the_last_owner_cannot_leave(self, team):
        response = send(
            team.owner, "DELETE", team.path(f"/members/{team.user_id(OWNER_EMAIL)}")
        )

        assert response.status_code == 409
        assert response.json()["last_owner"] is True
        assert team.owner.get(team.path()).status_code == 200

    def test_an_owner_can_step_down_once_there_is_another_owner(self, team):
        reviewer_id, owner_id = team.user_id(REVIEWER_EMAIL), team.user_id(OWNER_EMAIL)
        send(team.owner, "PATCH", team.path(f"/members/{reviewer_id}"), {"role": "owner"})

        response = send(
            team.owner, "PATCH", team.path(f"/members/{owner_id}"), {"role": "viewer"}
        )

        assert response.status_code == 200
        assert team.owner.get(team.path()).json()["role"] == "viewer"

    def test_the_last_owner_cannot_be_removed_by_a_co_owner_who_then_leaves(self, team):
        """Two owners, each acting once: whatever the order, one owner remains."""
        reviewer_id, owner_id = team.user_id(REVIEWER_EMAIL), team.user_id(OWNER_EMAIL)
        send(team.owner, "PATCH", team.path(f"/members/{reviewer_id}"), {"role": "owner"})

        removed = send(team.reviewer, "DELETE", team.path(f"/members/{owner_id}"))
        left = send(team.reviewer, "DELETE", team.path(f"/members/{reviewer_id}"))

        assert removed.status_code == 204
        assert left.status_code == 409
        assert team.reviewer.get(team.path()).json()["role"] == "owner"


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("PATCH", "", {"name": "Renamed"}),
        ("POST", "/archive", None),
        ("POST", "/unarchive", None),
        ("DELETE", "", None),
        ("POST", "/members", {"email": OUTSIDER_EMAIL, "role": "viewer"}),
        ("PATCH", "/members/{owner_id}", {"role": "viewer"}),
        ("DELETE", "/members/{owner_id}", None),
    ],
    ids=["rename", "archive", "unarchive", "delete", "add-member", "change-role", "remove"],
)
def test_owner_only_actions_are_refused_to_other_members_with_403(
    team, role, method, suffix, body
):
    """A member knows the project exists, so 403 hides nothing and explains more."""
    path = team.path(fill_path(suffix, owner_id=team.user_id(OWNER_EMAIL)))

    response = send(team.browser_for(role), method, path, body)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert team.owner.get(team.path()).json()["name"] == PROJECT_NAME


class TestDeleteProject:
    def test_an_owner_deletes_a_project_and_it_is_gone_for_everyone(self, team):
        response = send(team.owner, "DELETE", team.path())

        assert response.status_code == 204
        for browser in (team.owner, team.reviewer, team.viewer):
            assert browser.get(team.path()).status_code == 404
            assert browser.get(PROJECTS).json() == []

    def test_a_deleted_project_looks_exactly_like_one_that_never_existed(self, team):
        send(team.owner, "DELETE", team.path())

        deleted = team.owner.get(team.path())
        never_existed = team.owner.get(f"{PROJECTS}/{uuid.uuid4()}")

        assert deleted.status_code == never_existed.status_code == 404
        assert deleted.json() == never_existed.json()

    def test_deleting_destroys_the_projects_data_key(self, team):
        send(team.owner, "DELETE", team.path())

        assert team.harness.storage.state.data_keys[UUID(team.project_id)].is_destroyed

    def test_a_copy_taken_before_deletion_can_never_be_decrypted(self, team):
        """What a database backup holds: the ciphertext outlives the project, unreadably."""
        backup = stored_name_ciphertext(team.harness, team.project_id)
        assert decrypt_name(team.harness, team.project_id, backup) == PROJECT_NAME.encode()

        send(team.owner, "DELETE", team.path())

        with pytest.raises(KeyDestroyedError):
            decrypt_name(team.harness, team.project_id, backup)

    def test_the_audit_chain_still_verifies_after_deletion(self, team):
        send(team.owner, "PATCH", team.path(), {"name": "Cholera response, Aden"})
        send(team.owner, "DELETE", team.path())

        report = verify_audit(team.harness)

        assert report.is_intact
        assert project_events(team.harness, team.project_id)[-1].entry.action == (
            "project.deleted"
        )

    def test_deleting_removes_every_membership(self, team):
        """Who worked on what is itself sensitive; it goes with the project."""
        send(team.owner, "DELETE", team.path())

        assert stored_memberships(team.harness, team.project_id) == []

    def test_deleting_needs_a_sign_in_within_15_minutes(self, team):
        _stale_sign_in(team)

        refused = send(team.owner, "DELETE", team.path())
        key_survived_the_refusal = not team.harness.storage.state.data_keys[
            UUID(team.project_id)
        ].is_destroyed
        _reauthenticate(team.owner)
        accepted = send(team.owner, "DELETE", team.path())

        assert refused.status_code == 401
        assert refused.json()["reauthentication_required"] is True
        assert key_survived_the_refusal
        assert accepted.status_code == 204

    def test_a_non_member_cannot_delete(self, team):
        assert send(team.outsider, "DELETE", team.path()).status_code == 404
        assert team.owner.get(team.path()).status_code == 200

    def test_a_deleted_project_cannot_be_deleted_again(self, team):
        send(team.owner, "DELETE", team.path())

        assert send(team.owner, "DELETE", team.path()).status_code == 404

    def test_deleting_one_project_leaves_the_others_readable(self, team):
        other = create_project(team.owner, "Market assessment, Mopti")

        send(team.owner, "DELETE", team.path())

        assert team.owner.get(f"{PROJECTS}/{other['id']}").json()["name"] == (
            "Market assessment, Mopti"
        )


class TestAuditTrail:
    """Brief rule 16: every project action is on record, by id only."""

    def test_every_project_action_is_recorded_in_order(self, team):
        viewer_id, reviewer_id = team.user_id(VIEWER_EMAIL), team.user_id(REVIEWER_EMAIL)
        send(team.owner, "PATCH", team.path(), {"name": "Cholera response, Aden"})
        send(team.owner, "POST", team.path("/archive"))
        send(team.owner, "POST", team.path("/unarchive"))
        send(team.owner, "PATCH", team.path(f"/members/{viewer_id}"), {"role": "reviewer"})
        send(team.owner, "DELETE", team.path(f"/members/{reviewer_id}"))
        send(team.viewer, "DELETE", team.path(f"/members/{viewer_id}"))
        send(team.owner, "DELETE", team.path())

        actions = [e.entry.action for e in project_events(team.harness, team.project_id)]

        # Creating records only `project.created`: the owner's membership is implied.
        assert actions == [
            "project.created",
            "membership.added",
            "membership.added",
            "project.renamed",
            "project.archived",
            "project.unarchived",
            "membership.role_changed",
            "membership.removed",
            "membership.removed",
            "project.deleted",
        ]

    def test_each_event_names_who_acted_and_on_what(self, team):
        owner_id, viewer_id = team.user_id(OWNER_EMAIL), team.user_id(VIEWER_EMAIL)
        send(team.viewer, "DELETE", team.path(f"/members/{viewer_id}"))

        created, added_reviewer, added_viewer, viewer_left = [
            e.entry for e in project_events(team.harness, team.project_id)
        ]

        assert (created.actor_id, created.target_type, created.target_id) == (
            UUID(owner_id),
            "project",
            team.project_id,
        )
        assert (added_viewer.actor_id, added_viewer.target_type, added_viewer.target_id) == (
            UUID(owner_id),
            "user",
            viewer_id,
        )
        assert added_reviewer.target_id == team.user_id(REVIEWER_EMAIL)
        assert (viewer_left.actor_id, viewer_left.target_id) == (UUID(viewer_id), viewer_id)

    def test_events_carry_the_request_that_caused_them(self, team):
        response = team.owner.post(
            team.path("/archive"),
            headers={**csrf_headers(team.owner), "x-request-id": "req-archive-1"},
        )

        assert response.status_code == 200
        assert project_events(team.harness, team.project_id)[-1].entry.request_id == (
            "req-archive-1"
        )

    def test_audit_records_hold_ids_only_never_names_or_email_addresses(self, team):
        send(team.owner, "PATCH", team.path(), {"name": "Diphtheria response, Aden"})
        add_member(team.owner, team.project_id, OUTSIDER_EMAIL, "viewer")

        recorded = repr(project_events(team.harness, team.project_id))

        for personal in ("Cholera", "Diphtheria", "example.org", "kofi", "maya", "tariq"):
            assert personal not in recorded

    def test_refused_actions_are_never_recorded_as_done(self, team):
        send(team.viewer, "DELETE", team.path())
        send(team.outsider, "PATCH", team.path(), {"name": "Hijacked"})

        actions = [e.entry.action for e in project_events(team.harness, team.project_id)]

        assert "project.deleted" not in actions
        assert "project.renamed" not in actions
        assert team.owner.get(team.path()).json()["name"] == PROJECT_NAME


class TestVocabularyAndSearch:
    """The pre-project endpoints now sit behind sign-in; search runs inside a project."""

    def test_compiling_a_vocabulary_needs_a_signed_in_user_but_no_project(self, harness):
        anonymous = harness.browser().post("/api/v1/vocabulary/compile", json=VOCABULARY)
        member_of_nothing = signed_in(harness, OUTSIDER_EMAIL)

        response = send(member_of_nothing, "POST", "/api/v1/vocabulary/compile", VOCABULARY)

        assert anonymous.status_code == 401
        assert response.status_code == 200
        assert response.json()["term_count"] == 4

    def test_listing_sources_needs_a_signed_in_user(self, harness):
        assert harness.browser().get("/api/v1/sources").status_code == 401
        assert signed_in(harness, OUTSIDER_EMAIL).get("/api/v1/sources").status_code == 200

    @pytest.mark.parametrize("role", ["owner", "reviewer"])
    def test_owners_and_reviewers_search_within_their_project(self, team, role):
        response = send(
            team.browser_for(role), "POST", team.path("/search"), {**VOCABULARY, "limit": 10}
        )

        assert response.status_code == 200
        assert response.json()["source_id"] == "fake"
        assert response.json()["query"]["term_count"] == 4
        assert response.json()["total"] == len(response.json()["results"])

    def test_a_viewer_may_not_start_a_search(self, team):
        response = send(team.viewer, "POST", team.path("/search"), VOCABULARY)

        assert response.status_code == 403

    def test_a_non_member_cannot_search_in_the_project(self, team):
        assert send(team.outsider, "POST", team.path("/search"), VOCABULARY).status_code == 404

    def test_the_unscoped_search_route_no_longer_exists(self, harness):
        response = send(
            signed_in(harness, OUTSIDER_EMAIL), "POST", "/api/v1/search", VOCABULARY
        )

        assert response.status_code == 404
