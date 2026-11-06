"""Project masking rules over HTTP: "always mask this term in this project".

Sources: architecture §3.9.1 (`MaskingRule` applies to every current and future
document), UI_UX_SPEC §6.6 ("Always mask in this project"), §3.3 and
architecture §4.1 (only owners see raw text and manage documents), §5.4
(sealed under the project's key, shredded with it) and §5.5 (audited, ids only).

A rule's term is itself sensitive — typically a staff member's or a patient's
name — so it is treated like raw text: owners only, sealed at rest, never in
the audit log.
"""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import pytest

from app.domain.encryption import EncryptionContext, KeyDestroyedError
from tests.project_support import (
    OWNER_EMAIL,
    Team,
    make_team,
    project_events,
    send,
)

TERM = "Kofi Mensah"
TERM_PURPOSE = "masking_rules.term"


@pytest.fixture
def team() -> Team:
    return make_team()


def rules_path(team: Team, suffix: str = "") -> str:
    return team.path(f"/masking-rules{suffix}")


def add(team: Team, term: str = TERM, entity_type: str = "PERSON", browser=None):
    return send(
        browser or team.owner,
        "POST",
        rules_path(team),
        {"term": term, "entity_type": entity_type},
    )


def stored_rules(team: Team) -> list:
    async def read() -> list:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await uow.masking_rules.list_for_project(UUID(team.project_id))

    return asyncio.run(read())


def decrypt_term(team: Team, rule_id: UUID, sealed: bytes) -> bytes:
    context = EncryptionContext(UUID(team.project_id), TERM_PURPOSE, str(rule_id))

    async def decrypt() -> bytes:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await team.harness.providers.encryption.decrypt(uow, context, sealed)

    return asyncio.run(decrypt())


class TestOwnersManageRules:
    def test_a_new_project_has_no_rules(self, team):
        response = team.owner.get(rules_path(team))

        assert response.status_code == 200
        assert response.json() == []

    def test_an_owner_adds_a_rule_and_sees_it_listed(self, team):
        response = add(team, "  Kofi   Mensah ")

        assert response.status_code == 201
        body = response.json()
        assert body["term"] == TERM
        assert body["entity_type"] == "PERSON"
        assert body["created_by"] == team.user_id(OWNER_EMAIL)
        assert response.headers["Location"].endswith(f"/masking-rules/{body['id']}")
        assert team.owner.get(rules_path(team)).json() == [body]

    def test_a_rule_may_mask_a_term_as_any_entity_type(self, team):
        response = add(team, "Al Noor Clinic", "LOCATION")

        assert response.json()["entity_type"] == "LOCATION"

    def test_the_entity_type_defaults_to_person(self, team):
        response = send(team.owner, "POST", rules_path(team), {"term": TERM})

        assert response.json()["entity_type"] == "PERSON"

    def test_the_same_term_twice_is_a_conflict_whatever_its_case(self, team):
        add(team, TERM)

        response = add(team, TERM.upper())

        assert response.status_code == 409
        assert len(team.owner.get(rules_path(team)).json()) == 1

    def test_an_owner_removes_a_rule(self, team):
        rule_id = add(team).json()["id"]

        response = send(team.owner, "DELETE", rules_path(team, f"/{rule_id}"))

        assert response.status_code == 204
        assert team.owner.get(rules_path(team)).json() == []

    def test_removing_a_rule_twice_is_not_found(self, team):
        rule_id = add(team).json()["id"]
        send(team.owner, "DELETE", rules_path(team, f"/{rule_id}"))

        response = send(team.owner, "DELETE", rules_path(team, f"/{rule_id}"))

        assert response.status_code == 404

    def test_a_rule_of_another_project_cannot_be_removed_through_this_one(self, team):
        other = send(team.owner, "POST", "/api/v1/projects", {"name": "Other"}).json()["id"]
        foreign = send(
            team.owner,
            "POST",
            f"/api/v1/projects/{other}/masking-rules",
            {"term": TERM},
        ).json()["id"]

        response = send(team.owner, "DELETE", rules_path(team, f"/{foreign}"))

        assert response.status_code == 404
        assert len(team.owner.get(f"/api/v1/projects/{other}/masking-rules").json()) == 1


class TestValidation:
    @pytest.mark.parametrize("term", ["", "   ", "A", "...", "x" * 201, "Ko‮fi"])
    def test_an_unusable_term_is_refused_with_422(self, team, term):
        assert add(team, term).status_code == 422

    def test_an_unknown_entity_type_is_refused(self, team):
        assert add(team, TERM, "SECRET").status_code == 422

    def test_unknown_fields_are_refused(self, team):
        response = send(
            team.owner,
            "POST",
            rules_path(team),
            {"term": TERM, "created_by": str(uuid.uuid4())},
        )

        assert response.status_code == 422


class TestOnlyOwners:
    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_other_members_can_neither_read_add_nor_remove_rules(self, team, role):
        """A rule's term is raw text: only owners see it (architecture §3.9.1)."""
        rule_id = add(team).json()["id"]
        browser = team.browser_for(role)

        assert browser.get(rules_path(team)).status_code == 403
        assert add(team, "Amina", browser=browser).status_code == 403
        assert send(browser, "DELETE", rules_path(team, f"/{rule_id}")).status_code == 403
        assert len(stored_rules(team)) == 1

    def test_a_non_member_learns_nothing(self, team):
        rule_id = add(team).json()["id"]

        assert team.outsider.get(rules_path(team)).status_code == 404
        assert send(team.outsider, "DELETE", rules_path(team, f"/{rule_id}")).status_code == 404


class TestAtRest:
    def test_the_term_is_stored_only_sealed_under_the_projects_key(self, team):
        rule_id = UUID(add(team).json()["id"])

        (stored,) = stored_rules(team)

        assert TERM.encode() not in stored.term_sealed
        assert decrypt_term(team, rule_id, stored.term_sealed) == TERM.encode()

    def test_a_sealed_term_cannot_be_moved_to_another_rule(self, team):
        add(team)
        (stored,) = stored_rules(team)

        with pytest.raises(Exception):  # noqa: B017, PT011 — any authentication failure
            decrypt_term(team, uuid.uuid4(), stored.term_sealed)

    def test_deleting_the_project_makes_every_term_unreadable(self, team):
        rule_id = UUID(add(team).json()["id"])
        (stored,) = stored_rules(team)

        send(team.owner, "DELETE", team.path())

        with pytest.raises(KeyDestroyedError):
            decrypt_term(team, rule_id, stored.term_sealed)


class TestAudit:
    def test_adding_viewing_and_removing_are_audited_with_ids_only(self, team):
        rule_id = add(team).json()["id"]
        team.owner.get(rules_path(team))
        send(team.owner, "DELETE", rules_path(team, f"/{rule_id}"))

        events = project_events(team.harness, team.project_id)
        mine = [e.entry for e in events if e.entry.action.startswith("masking_rule")]

        assert [(e.action, e.target_type) for e in mine] == [
            ("masking_rule.created", "masking_rule"),
            ("masking_rule.listed", "project"),
            ("masking_rule.deleted", "masking_rule"),
        ]
        assert mine[0].target_id == rule_id
        assert mine[2].target_id == rule_id
        assert "Kofi" not in repr(events)
        assert "Mensah" not in repr(events)

    def test_a_refused_attempt_is_not_recorded_as_done(self, team):
        add(team, browser=team.reviewer)
        add(team, "")

        actions = [e.entry.action for e in project_events(team.harness, team.project_id)]

        assert "masking_rule.created" not in actions
