"""Requirements for projects, membership and the role policy, as pure domain rules.

The table below is the permission model, so changing who may do what is a visible,
reviewed diff to it. Anything not in it is denied.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.projects import (
    Action,
    LastOwnerError,
    Membership,
    Project,
    ProjectStatus,
    Role,
    can,
    normalize_project_name,
    project_status,
    require_an_owner_remains,
)
from app.domain.reviews import ReviewStatus

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)

OWNER, REVIEWER, VIEWER = Role.OWNER, Role.REVIEWER, Role.VIEWER

# One row per action. Rename and archive are owner-only: the UI spec groups them
# with "manage tags, collection" under project settings.
PERMISSIONS: dict[Action, frozenset[Role]] = {
    Action.READ_PROJECT: frozenset({OWNER, REVIEWER, VIEWER}),
    Action.ASK_QUESTIONS: frozenset({OWNER, REVIEWER}),
    Action.EDIT_REVIEW: frozenset({OWNER, REVIEWER}),
    Action.MANAGE_RUNS: frozenset({OWNER, REVIEWER}),
    Action.CONFIRM_GATE: frozenset({OWNER, REVIEWER}),
    Action.EDIT_LABELS: frozenset({OWNER, REVIEWER}),
    Action.MANAGE_DOCUMENTS: frozenset({OWNER}),
    Action.VIEW_RAW_TEXT: frozenset({OWNER}),
    Action.MANAGE_MEMBERS: frozenset({OWNER}),
    Action.UPDATE_PROJECT: frozenset({OWNER}),
    Action.DELETE_PROJECT: frozenset({OWNER}),
    Action.LEAVE_PROJECT: frozenset({OWNER, REVIEWER, VIEWER}),
}

_EVERY_PAIR = [(role, action) for action in Action for role in Role]


class TestRoles:
    def test_there_are_exactly_three_project_roles(self):
        assert {role.value for role in Role} == {"owner", "reviewer", "viewer"}


class TestPolicy:
    def test_the_table_decides_every_action(self):
        """A new action cannot be added without deciding, here, who may take it."""
        assert set(PERMISSIONS) == set(Action)

    @pytest.mark.parametrize(
        ("role", "action"), _EVERY_PAIR, ids=[f"{r.value}-{a.value}" for r, a in _EVERY_PAIR]
    )
    def test_each_role_may_do_exactly_what_the_table_allows(self, role, action):
        assert can(role, action) is (role in PERMISSIONS[action])

    @pytest.mark.parametrize("action", list(Action), ids=lambda a: a.value)
    def test_a_non_member_may_do_nothing(self, action):
        assert can(None, action) is False

    @pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
    def test_an_action_the_policy_does_not_know_is_denied(self, role):
        """Deny by default: an unlisted action is refused, not an error and not allowed."""
        assert can(role, cast("Action", "project.launch")) is False

    def test_a_viewer_may_only_read_and_leave(self):
        allowed = {action for action in Action if can(VIEWER, action)}

        assert allowed == {Action.READ_PROJECT, Action.LEAVE_PROJECT}

    def test_only_owners_may_manage_members_documents_or_delete(self):
        for action in (Action.MANAGE_MEMBERS, Action.MANAGE_DOCUMENTS, Action.DELETE_PROJECT):
            assert [role for role in Role if can(role, action)] == [OWNER]

    def test_an_owner_may_do_everything(self):
        assert all(can(OWNER, action) for action in Action)


class TestProjectName:
    def test_surrounding_whitespace_is_trimmed(self):
        assert normalize_project_name("  Cash vs vouchers, Gao \n") == "Cash vs vouchers, Gao"

    def test_any_script_is_accepted(self):
        assert normalize_project_name("Réponse au choléra, Gao") == "Réponse au choléra, Gao"
        assert normalize_project_name("الاستجابة للكوليرا") == "الاستجابة للكوليرا"

    @pytest.mark.parametrize("name", ["", "   ", "\t\n"])
    def test_a_blank_name_is_refused(self, name):
        with pytest.raises(ValueError, match=r"Project\.name"):
            normalize_project_name(name)

    def test_a_name_of_200_characters_is_accepted(self):
        assert len(normalize_project_name("x" * 200)) == 200

    def test_a_name_over_200_characters_is_refused(self):
        with pytest.raises(ValueError, match=r"Project\.name"):
            normalize_project_name("x" * 201)

    @pytest.mark.parametrize("name", ["Gao\x00", "Gao\nresponse", "Gao‮esnopser"])
    def test_control_characters_inside_the_name_are_refused(self, name):
        """They break logs and exports, and the bidi override can disguise a name."""
        with pytest.raises(ValueError, match=r"Project\.name"):
            normalize_project_name(name)


def _project(**overrides: object) -> Project:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "name_sealed": b"sealed-name",
        "created_at": NOW,
        "created_by": uuid.uuid4(),
    }
    values.update(overrides)
    return Project(**values)  # type: ignore[arg-type]


class TestProject:
    def test_a_new_project_is_neither_archived_nor_deleted(self):
        project = _project()

        assert not project.is_archived
        assert not project.is_deleted
        assert project.version == 0

    def test_timestamps_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _project(created_at=datetime(2026, 11, 3, 9, 0))  # noqa: DTZ001 — the invalid input under test

    def test_the_name_is_held_only_as_ciphertext(self):
        """The domain never holds a project's name in the clear; it is sealed under its key."""
        assert not hasattr(_project(), "name")

    def test_an_empty_ciphertext_is_refused(self):
        with pytest.raises(ValueError, match="name_sealed"):
            _project(name_sealed=b"")

    def test_archiving_records_when_and_unarchiving_clears_it(self):
        archived = _project().archived(NOW)

        assert archived.is_archived
        assert archived.archived_at == NOW
        assert not archived.unarchived().is_archived

    def test_archiving_an_archived_project_keeps_the_first_date(self):
        archived = _project().archived(NOW)

        assert archived.archived(NOW + timedelta(days=1)).archived_at == NOW

    def test_renaming_replaces_only_the_ciphertext(self):
        project = _project()

        renamed = project.renamed(b"another-sealed-name")

        assert renamed.name_sealed == b"another-sealed-name"
        assert replace(renamed, name_sealed=project.name_sealed) == project

    def test_deleting_records_when(self):
        deleted = _project().deleted(NOW)

        assert deleted.is_deleted
        assert deleted.deleted_at == NOW


PROJECT_ID = uuid.uuid4()


def _member(role: Role, user_id: uuid.UUID | None = None) -> Membership:
    return Membership(
        project_id=PROJECT_ID,
        user_id=user_id or uuid.uuid4(),
        role=role,
        added_at=NOW,
        added_by=None,
    )


class TestMembership:
    def test_added_at_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Membership(
                project_id=PROJECT_ID,
                user_id=uuid.uuid4(),
                role=VIEWER,
                added_at=datetime(2026, 11, 3, 9, 0),  # noqa: DTZ001 — the invalid input under test
                added_by=None,
            )

    def test_changing_the_role_keeps_everything_else(self):
        member = _member(VIEWER)

        promoted = member.with_role(REVIEWER)

        assert promoted.role is REVIEWER
        assert replace(promoted, role=VIEWER) == member


class TestLastOwnerRule:
    """The last owner can't leave or be demoted."""

    def test_demoting_the_only_owner_is_refused(self):
        owner = _member(OWNER)
        members = [owner, _member(REVIEWER)]

        with pytest.raises(LastOwnerError):
            require_an_owner_remains(members, owner.user_id, REVIEWER)

    def test_removing_the_only_owner_is_refused(self):
        owner = _member(OWNER)

        with pytest.raises(LastOwnerError):
            require_an_owner_remains([owner, _member(VIEWER)], owner.user_id, None)

    def test_an_owner_may_step_down_while_another_owner_remains(self):
        first, second = _member(OWNER), _member(OWNER)

        require_an_owner_remains([first, second], first.user_id, VIEWER)
        require_an_owner_remains([first, second], first.user_id, None)

    def test_changing_someone_who_is_not_an_owner_never_trips_the_rule(self):
        reviewer = _member(REVIEWER)
        members = [_member(OWNER), reviewer]

        require_an_owner_remains(members, reviewer.user_id, VIEWER)
        require_an_owner_remains(members, reviewer.user_id, None)

    def test_keeping_the_only_owner_an_owner_is_allowed(self):
        owner = _member(OWNER)

        require_an_owner_remains([owner], owner.user_id, OWNER)

    @given(
        changes=st.lists(
            st.tuples(st.integers(0, 3), st.sampled_from([OWNER, REVIEWER, VIEWER, None])),
            max_size=20,
        )
    )
    def test_no_sequence_of_allowed_changes_leaves_a_project_without_an_owner(self, changes):
        members = {m.user_id: m for m in [_member(OWNER)] + [_member(VIEWER) for _ in range(3)]}
        ids = list(members)

        for index, new_role in changes:
            target = ids[index]
            if target not in members:
                continue
            try:
                require_an_owner_remains(list(members.values()), target, new_role)
            except LastOwnerError:
                continue
            if new_role is None:
                del members[target]
            else:
                members[target] = members[target].with_role(new_role)

        assert any(m.role is OWNER for m in members.values())


# UI_UX_SPEC §11.1, one row per review status. A cancelled run is not in the spec's table;
# it gets its own status rather than borrowing one that would say something untrue.
STATUS_BY_REVIEW: dict[ReviewStatus, ProjectStatus] = {
    ReviewStatus.NEW: ProjectStatus.PLANNING,
    ReviewStatus.CLARIFYING: ProjectStatus.PLANNING,
    ReviewStatus.DRAFTING: ProjectStatus.PLANNING,
    ReviewStatus.CHANGES_REQUESTED: ProjectStatus.PLANNING,
    ReviewStatus.ACCEPTED: ProjectStatus.PLANNING,
    ReviewStatus.RUNNING: ProjectStatus.RUNNING,
    ReviewStatus.WAITING: ProjectStatus.NEEDS_YOU,
    ReviewStatus.FAILED: ProjectStatus.FAILED,
    ReviewStatus.CANCELLED: ProjectStatus.CANCELLED,
}


class TestProjectStatus:
    def test_every_review_status_decides_a_project_status(self):
        assert set(STATUS_BY_REVIEW) | {ReviewStatus.DONE} == set(ReviewStatus)

    @pytest.mark.parametrize(("review", "expected"), STATUS_BY_REVIEW.items())
    def test_the_latest_reviews_state_is_the_projects_status(self, review, expected):
        assert (
            project_status(is_archived=False, latest_review=review, has_brief=False) is expected
        )

    def test_a_finished_run_with_a_brief_is_brief_ready(self):
        status = project_status(
            is_archived=False, latest_review=ReviewStatus.DONE, has_brief=True
        )

        assert status is ProjectStatus.BRIEF_READY

    def test_a_finished_run_without_the_brief_stage_is_done_with_no_brief(self):
        status = project_status(
            is_archived=False, latest_review=ReviewStatus.DONE, has_brief=False
        )

        assert status is ProjectStatus.DONE_WITHOUT_BRIEF

    def test_a_project_with_no_review_yet_is_planning(self):
        status = project_status(is_archived=False, latest_review=None, has_brief=False)

        assert status is ProjectStatus.PLANNING

    @pytest.mark.parametrize("review", [None, *ReviewStatus])
    def test_archived_wins_over_every_review_state(self, review):
        status = project_status(is_archived=True, latest_review=review, has_brief=True)

        assert status is ProjectStatus.ARCHIVED
