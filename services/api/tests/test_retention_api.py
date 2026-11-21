"""Requirements for the retention period over HTTP and in the worker (UI_UX_SPEC §7.7).

The operator may set a period; without one nothing is ever deleted for age. With one, a
project is deleted that long after its last activity unless an owner keeps it, owners are
warned in Needs you for the last 14 days, and the deletion is exactly a normal one: the
project's key is destroyed, so everything sealed under it is unreadable. The clock is the
harness's own, moved by hand.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.encryption import KeyDestroyedError
from app.settings import Environment, Settings
from tests.brief_support import stored_brief, write_brief
from tests.history_support import add_paper
from tests.project_support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    OUTSIDER_EMAIL,
    OWNER_EMAIL,
    PROJECT_NAME,
    PROJECTS,
    REVIEWER_EMAIL,
    VIEWER_EMAIL,
    Team,
    audit_events,
    create_project,
    decrypt_name,
    make_team,
    project_events,
    send,
    signed_in,
    stored_memberships,
    stored_name_ciphertext,
    verify_audit,
)
from tests.question_support import make_ask_team
from tests.run_support import create_review, make_run_team
from tests.support import FakeClock

POLICY = "/api/v1/retention"
NEEDS_YOU = "/api/v1/needs-you"
START = FakeClock().now
YEAR = timedelta(days=365)


def retention_team(period_days: int | None = 365) -> Team:
    return make_team(retention_days=period_days)


@pytest.fixture
def team() -> Team:
    return retention_team()


def later(team: Team, **delta: float) -> None:
    """Move the clock on; everyone signs in again, since their sessions have expired."""
    team.harness.clock.advance(**delta)
    harness = team.harness
    team.owner = signed_in(harness, OWNER_EMAIL)
    team.reviewer = signed_in(harness, REVIEWER_EMAIL)
    team.viewer = signed_in(harness, VIEWER_EMAIL)
    team.outsider = signed_in(harness, OUTSIDER_EMAIL)
    team.admin = signed_in(harness, ADMIN_EMAIL, ADMIN_PASSWORD)


def retention(team: Team, browser: Any = None) -> dict[str, Any]:
    response = (browser or team.owner).get(team.path("/retention"))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def keep(team: Team, browser: Any = None) -> Any:
    return send(browser or team.owner, "POST", team.path("/retention/keep"))


def at(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def notices(browser: Any) -> list[dict[str, Any]]:
    response = browser.get(NEEDS_YOU)
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"] if item["kind"] == "retention"]


def delete_due(team: Team) -> int:
    deleted: int = asyncio.run(team.harness.providers.retention.delete_due())
    return deleted


class TestTheSetting:
    def test_retention_is_off_unless_the_operator_sets_a_period(self):
        assert Settings(environment=Environment.TEST).retention_days is None

    @pytest.mark.parametrize("days", [0, 7, 29])
    def test_a_period_under_30_days_is_refused_at_start(self, days):
        with pytest.raises(ValidationError, match="retention_days"):
            Settings(environment=Environment.TEST, retention_days=days)


class TestTheDeploymentsPeriod:
    def test_without_a_period_everyone_is_told_nothing_is_deleted_for_age(self):
        team = retention_team(None)

        response = team.admin.get(POLICY)

        assert response.status_code == 200
        assert response.json() == {"period_days": None, "warning_days": 14}

    def test_owners_and_admins_read_the_period_the_operator_set(self, team):
        for browser in (team.admin, team.owner):
            assert browser.get(POLICY).json() == {"period_days": 365, "warning_days": 14}

    def test_reading_the_period_needs_a_sign_in(self, team):
        assert team.harness.browser().get(POLICY).status_code == 401


class TestAProjectsDate:
    def test_without_a_period_a_project_has_no_deletion_date(self):
        team = retention_team(None)

        assert retention(team) == {
            "period_days": None,
            "deletes_at": None,
            "days_left": None,
            "kept_at": None,
        }

    def test_every_member_sees_the_date_counted_from_the_last_activity(self, team):
        for browser in (team.owner, team.reviewer, team.viewer):
            body = retention(team, browser)

            assert body["period_days"] == 365
            assert at(body["deletes_at"]) == START + YEAR
            assert body["days_left"] == 365
            assert body["kept_at"] is None

    def test_new_work_in_the_project_restarts_the_clock(self, team):
        later(team, days=200)

        create_review(team.owner, team.project_id)

        assert at(retention(team)["deletes_at"]) == START + timedelta(days=200) + YEAR

    def test_opening_a_paper_restarts_the_clock(self, team):
        review = create_review(team.owner, team.project_id)
        paper = add_paper(team.harness, team.project_id, review["id"])
        later(team, days=200)

        assert send(team.viewer, "PUT", team.path(f"/papers/{paper}/seen")).status_code == 204

        assert at(retention(team)["deletes_at"]) == START + timedelta(days=200) + YEAR

    def test_opening_the_brief_restarts_the_clock(self):
        rt = make_run_team(retention_days=365)
        brief = stored_brief(rt, write_brief(rt))
        assert brief is not None
        later(rt.team, days=200)

        assert send(rt.team.viewer, "PUT", f"/api/v1/briefs/{brief.id}/seen").status_code == 204

        assert at(retention(rt.team)["deletes_at"]) == START + timedelta(days=200) + YEAR

    def test_asking_a_follow_up_question_restarts_the_clock(self):
        ask = make_ask_team(retention_days=365)
        later(ask.rt.team, days=200)

        assert ask.ask().status_code == 202

        assert at(retention(ask.rt.team)["deletes_at"]) == START + timedelta(days=200) + YEAR

    def test_reading_moves_the_date_at_most_once_a_day(self, team):
        review = create_review(team.owner, team.project_id)
        paper = add_paper(team.harness, team.project_id, review["id"])
        seen = team.path(f"/papers/{paper}/seen")
        later(team, days=200)
        send(team.viewer, "PUT", seen)

        later(team, hours=12)
        send(team.viewer, "PUT", seen)
        assert at(retention(team)["deletes_at"]) == START + timedelta(days=200) + YEAR

        later(team, hours=13)
        send(team.viewer, "PUT", seen)
        assert at(retention(team)["deletes_at"]) == START + timedelta(days=201, hours=1) + YEAR

    def test_a_non_member_cannot_tell_the_project_exists(self, team):
        assert team.outsider.get(team.path("/retention")).status_code == 404
        assert team.admin.get(team.path("/retention")).status_code == 404


class TestKeep:
    def test_an_owner_keeps_the_project_and_its_clock_restarts(self, team):
        later(team, days=360)

        response = keep(team)

        assert response.status_code == 200, response.text
        kept = START + timedelta(days=360)
        assert at(response.json()["kept_at"]) == kept
        assert at(response.json()["deletes_at"]) == kept + YEAR
        assert response.json()["days_left"] == 365
        assert retention(team, team.viewer)["deletes_at"] == response.json()["deletes_at"]

    def test_reviewers_and_viewers_cannot_keep_and_outsiders_get_404(self, team):
        assert keep(team, team.reviewer).status_code == 403
        assert keep(team, team.viewer).status_code == 403
        assert keep(team, team.outsider).status_code == 404
        assert retention(team)["kept_at"] is None

    def test_keeping_is_refused_when_the_deployment_deletes_nothing_for_age(self):
        team = retention_team(None)

        response = keep(team)

        assert response.status_code == 409
        assert "does not delete projects" in response.json()["detail"]

    def test_keeping_is_audited_with_ids_only(self, team):
        keep(team)

        event = project_events(team.harness, team.project_id)[-1].entry
        assert event.action == "project.kept"
        assert event.actor_id == UUID(team.user_id(OWNER_EMAIL))
        assert event.target_id == team.project_id
        assert event.details_sealed is None

    def test_a_deleted_project_cannot_be_kept(self, team):
        send(team.owner, "DELETE", team.path())

        assert keep(team).status_code == 404


class TestTheNotice:
    def test_owners_see_the_real_number_of_days_in_needs_you(self, team):
        later(team, days=351)

        (item,) = notices(team.owner)

        assert item["project"] == {"id": team.project_id, "name": PROJECT_NAME}
        assert item["review_id"] is None
        assert item["days_left"] == 14
        assert at(item["deletes_at"]) == START + YEAR
        assert item["key"] == f"retention:{team.project_id}:2027-10-24"

    def test_a_project_further_from_its_date_shows_nothing(self, team):
        later(team, days=350)

        assert notices(team.owner) == []

    def test_the_notice_waits_only_for_owners_who_can_keep(self, team):
        later(team, days=360)

        assert len(notices(team.owner)) == 1
        for browser in (team.reviewer, team.viewer, team.outsider, team.admin):
            assert notices(browser) == []

    def test_an_archived_project_is_still_announced(self, team):
        send(team.owner, "POST", team.path("/archive"))
        later(team, days=360)

        assert [item["project"]["id"] for item in notices(team.owner)] == [team.project_id]

    def test_keeping_clears_the_notice(self, team):
        later(team, days=360)

        keep(team)

        assert notices(team.owner) == []

    def test_a_dismissed_notice_stays_hidden(self, team):
        later(team, days=360)
        (item,) = notices(team.owner)

        dismissed = send(
            team.owner, "POST", "/api/v1/needs-you/dismissals", {"key": item["key"]}
        )

        assert dismissed.status_code == 204
        assert notices(team.owner) == []

    def test_without_a_period_no_project_is_ever_announced(self):
        team = retention_team(None)
        later(team, days=3000)

        assert notices(team.owner) == []


class TestDeletionWhenDue:
    def test_nothing_is_deleted_before_its_date(self, team):
        team.harness.clock.advance(days=364, hours=23)

        assert delete_due(team) == 0
        assert not team.harness.storage.state.data_keys[UUID(team.project_id)].is_destroyed

    def test_a_project_past_its_date_is_deleted_like_any_other(self, team):
        later(team, days=366)

        assert delete_due(team) == 1

        assert team.harness.storage.state.data_keys[UUID(team.project_id)].is_destroyed
        assert stored_memberships(team.harness, team.project_id) == []
        for browser in (team.owner, team.reviewer, team.viewer):
            assert browser.get(team.path()).status_code == 404
            assert browser.get(PROJECTS).json() == []

    def test_a_copy_taken_before_the_deletion_can_never_be_decrypted(self, team):
        backup = stored_name_ciphertext(team.harness, team.project_id)
        team.harness.clock.advance(days=366)

        delete_due(team)

        with pytest.raises(KeyDestroyedError):
            decrypt_name(team.harness, team.project_id, backup)

    def test_the_deletion_is_audited_as_a_deletion_with_its_reason(self, team):
        team.harness.clock.advance(days=366)

        delete_due(team)

        expired, deleted = [e.entry for e in project_events(team.harness, team.project_id)][-2:]
        assert (expired.action, deleted.action) == (
            "project.retention_expired",
            "project.deleted",
        )
        assert expired.actor_id is None
        assert deleted.actor_id is None
        assert expired.target_id == deleted.target_id == team.project_id
        assert expired.details_sealed is None
        assert verify_audit(team.harness).is_intact

    def test_running_it_twice_deletes_once(self, team):
        team.harness.clock.advance(days=366)

        assert delete_due(team) == 1
        assert delete_due(team) == 0

        actions = [e.entry.action for e in audit_events(team.harness)]
        assert actions.count("project.deleted") == 1

    def test_a_keep_that_lands_after_the_project_was_found_due_wins(self, team):
        later(team, days=366)
        retention_service = team.harness.providers.retention
        (due,) = asyncio.run(retention_service.due_projects())

        assert keep(team).status_code == 200
        is_deleted = asyncio.run(retention_service.delete_if_due(due))

        assert not is_deleted
        assert team.owner.get(team.path()).status_code == 200

    def test_a_kept_project_is_not_deleted_on_its_old_date(self, team):
        later(team, days=360)
        keep(team)
        team.harness.clock.advance(days=10)

        assert delete_due(team) == 0
        assert not team.harness.storage.state.data_keys[UUID(team.project_id)].is_destroyed

    def test_a_project_people_still_read_is_not_deleted_on_its_old_date(self, team):
        review = create_review(team.owner, team.project_id)
        paper = add_paper(team.harness, team.project_id, review["id"])
        later(team, days=300)
        send(team.viewer, "PUT", team.path(f"/papers/{paper}/seen"))
        later(team, days=100)

        assert delete_due(team) == 0
        assert team.viewer.get(team.path()).status_code == 200

    def test_only_projects_past_their_date_are_deleted(self, team):
        later(team, days=200)
        other = create_project(team.owner, "Market assessment, Mopti")
        team.harness.clock.advance(days=166)

        assert delete_due(team) == 1

        state = team.harness.storage.state
        assert state.data_keys[UUID(team.project_id)].is_destroyed
        assert not state.data_keys[UUID(other["id"])].is_destroyed

    def test_without_a_period_nothing_is_deleted_however_old(self):
        team = retention_team(None)
        team.harness.clock.advance(days=5000)

        assert delete_due(team) == 0
        assert not team.harness.storage.state.data_keys[UUID(team.project_id)].is_destroyed
