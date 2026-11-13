"""Requirements for a project's instructions to the model over HTTP (ADR 0015).

Every member may read them; owners save them. Each save is a new version and older ones
stay. The text is person-typed and goes to a model, so a term the project always masks is
refused, and the text is sealed under the project's key.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.domain.encryption import EncryptionContext
from app.domain.model_instructions import MAX_INSTRUCTIONS_LENGTH
from tests.project_support import OWNER_EMAIL, Team, make_team, project_events, send

SCREENING = "Treat studies in refugee camps as displaced settings."
WRITING = "Write for a field manager: short sentences, no jargon."
TERM = "Kofi Mensah"


@pytest.fixture
def team() -> Team:
    return make_team()


def path(team: Team) -> str:
    return team.path("/model-instructions")


def save(
    team: Team,
    screening: str = SCREENING,
    writing: str = WRITING,
    based_on: int | None = None,
    browser: Any = None,
) -> Any:
    body = {"screening": screening, "writing": writing, "based_on_version": based_on}
    return send(browser or team.owner, "PUT", path(team), body)


def stored_versions(team: Team) -> list[Any]:
    async def read() -> list[Any]:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await uow.model_instructions.list_for_project(UUID(team.project_id))

    return asyncio.run(read())


class TestReading:
    def test_a_new_project_has_no_instructions(self, team):
        response = team.owner.get(path(team))

        assert response.status_code == 200
        assert response.json() == {"current": None, "versions": []}

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_every_member_may_read_them(self, team, role):
        save(team)

        response = team.browser_for(role).get(path(team))

        assert response.status_code == 200
        assert response.json()["current"]["screening"] == SCREENING

    def test_a_non_member_learns_nothing(self, team):
        save(team)

        assert team.outsider.get(path(team)).status_code == 404


class TestSaving:
    def test_an_owner_saves_both_texts_as_version_one(self, team):
        response = save(team)

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 1
        assert body["screening"] == SCREENING
        assert body["writing"] == WRITING
        assert body["saved_by"] == {
            "id": team.user_id(OWNER_EMAIL),
            "display_name": OWNER_EMAIL.split("@")[0],
        }
        assert team.owner.get(path(team)).json() == {"current": body, "versions": [body]}

    def test_each_save_is_a_new_version_and_older_ones_stay(self, team):
        first = save(team).json()

        second = save(team, writing="Use plain words.", based_on=1).json()

        listed = team.owner.get(path(team)).json()
        assert second["version"] == 2
        assert listed["current"] == second
        assert listed["versions"] == [first, second]

    def test_blank_text_clears_the_instructions_as_a_new_version(self, team):
        save(team)

        response = save(team, screening="  ", writing="", based_on=1)

        assert response.status_code == 200
        assert response.json()["screening"] == ""
        assert response.json()["writing"] == ""
        assert response.json()["version"] == 2

    def test_a_save_based_on_an_older_version_is_a_conflict_and_stores_nothing(self, team):
        save(team)
        save(team, writing="Someone else's edit.", based_on=1)

        response = save(team, writing="My edit.", based_on=1)

        assert response.status_code == 409
        assert [v.version for v in stored_versions(team)] == [1, 2]

    def test_a_first_save_when_someone_saved_first_is_a_conflict(self, team):
        save(team)

        assert save(team, based_on=None).status_code == 409

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_only_owners_may_save(self, team, role):
        response = save(team, browser=team.browser_for(role))

        assert response.status_code == 403
        assert stored_versions(team) == []

    def test_a_non_member_gets_404(self, team):
        assert save(team, browser=team.outsider).status_code == 404


class TestValidation:
    @pytest.mark.parametrize("field", ["screening", "writing"])
    def test_a_text_over_the_limit_is_refused_naming_its_field(self, team, field):
        texts = {"screening": "", "writing": ""} | {field: "x" * (MAX_INSTRUCTIONS_LENGTH + 1)}

        response = save(team, screening=texts["screening"], writing=texts["writing"])

        assert response.status_code == 422
        assert field in response.text
        assert stored_versions(team) == []

    @pytest.mark.parametrize("field", ["screening", "writing"])
    def test_a_term_the_project_always_masks_is_refused_without_echoing_it(self, team, field):
        send(team.owner, "POST", team.path("/masking-rules"), {"term": TERM})
        texts = {"screening": "", "writing": ""} | {field: f"Ask {TERM} about camps."}

        response = save(team, screening=texts["screening"], writing=texts["writing"])

        assert response.status_code == 422
        assert field in response.text
        assert "Kofi" not in response.text
        assert stored_versions(team) == []

    def test_unknown_fields_are_refused(self, team):
        body = {"screening": "", "writing": "", "based_on_version": None, "version": 9}

        assert send(team.owner, "PUT", path(team), body).status_code == 422


class TestAtRest:
    def test_the_texts_are_stored_only_sealed_under_the_projects_key(self, team):
        save(team)

        (stored,) = stored_versions(team)

        assert SCREENING.encode() not in stored.instructions_sealed
        assert WRITING.encode() not in stored.instructions_sealed
        context = EncryptionContext(
            UUID(team.project_id), "model_instruction_versions.instructions", "1"
        )

        async def decrypt() -> bytes:
            async with team.harness.providers.storage.unit_of_work() as uow:
                return await team.harness.providers.encryption.decrypt(
                    uow, context, stored.instructions_sealed
                )

        assert SCREENING.encode() in asyncio.run(decrypt())


def audit_details(team: Team, event: Any) -> Any:
    async def read() -> Any:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await team.harness.providers.audit.read_details(uow, event)

    return asyncio.run(read())


class TestAudit:
    def test_a_save_is_audited_with_its_version_and_never_the_text(self, team):
        save(team)

        events = project_events(team.harness, team.project_id)
        mine = [e for e in events if e.entry.action == "project.model_instructions_changed"]

        assert len(mine) == 1
        assert mine[0].entry.target_type == "project"
        assert mine[0].entry.target_id == team.project_id
        assert audit_details(team, mine[0]) == {"version": 1}
        assert "refugee" not in repr(events)
        assert "field manager" not in repr(events)

    def test_a_refused_save_is_not_audited(self, team):
        save(team, browser=team.reviewer)
        save(team, screening="x" * (MAX_INSTRUCTIONS_LENGTH + 1))

        actions = [e.entry.action for e in project_events(team.harness, team.project_id)]

        assert "project.model_instructions_changed" not in actions
