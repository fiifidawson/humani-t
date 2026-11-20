"""Requirements for "Your view first" over HTTP (UI_UX_SPEC §6.9, §16 Q3).

An owner turns it on per project; it is off by default. Then each member may say, once
per brief, which option they lean towards before reading it. Only they see their answer;
it is sealed under the project's key, and the audit log holds ids only.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.domain.encryption import DecryptionError, EncryptionContext, KeyDestroyedError
from app.domain.leanings import StoredLeaning
from tests.brief_support import write_brief
from tests.project_support import OWNER_EMAIL, PROJECTS, project_events, send, user_id
from tests.run_support import RunTeam, make_run_team

CASH = "Cash transfers"


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def switch(rt: RunTeam, is_on: bool, browser: TestClient | None = None) -> Any:
    body = {"is_view_first_on": is_on}
    return send(browser or rt.owner, "PUT", f"{PROJECTS}/{rt.project_id}/view-first", body)


def leaning_path(run_id: str) -> str:
    return f"/api/v1/runs/{run_id}/leaning"


def answer(rt: RunTeam, run_id: str, option: str | None, browser: TestClient | None = None):
    return send(browser or rt.owner, "PUT", leaning_path(run_id), {"option": option})


def asked(rt: RunTeam) -> str:
    """A project that asks for your view first, and a run with a brief."""
    assert switch(rt, True).status_code == 200
    return write_brief(rt)


def stored(rt: RunTeam, run_id: str, email: str) -> StoredLeaning | None:
    async def read() -> StoredLeaning | None:
        async with rt.harness.providers.storage.unit_of_work() as uow:
            return await uow.leanings.get(UUID(run_id), UUID(user_id(rt.harness, email)))

    return asyncio.run(read())


class TestTheProjectSetting:
    def test_it_is_off_for_a_new_project(self, rt):
        project = rt.owner.get(f"{PROJECTS}/{rt.project_id}").json()

        assert project["is_view_first_on"] is False

    def test_an_owner_turns_it_on_and_off(self, rt):
        on = switch(rt, True)

        assert on.status_code == 200
        assert on.json()["is_view_first_on"] is True
        assert rt.viewer.get(f"{PROJECTS}/{rt.project_id}").json()["is_view_first_on"] is True
        assert switch(rt, False).json()["is_view_first_on"] is False

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_only_owners_may_change_it(self, rt, role):
        response = switch(rt, True, browser=rt.team.browser_for(role))

        assert response.status_code == 403
        assert rt.owner.get(f"{PROJECTS}/{rt.project_id}").json()["is_view_first_on"] is False

    def test_a_non_member_gets_404(self, rt):
        assert switch(rt, True, browser=rt.outsider).status_code == 404

    def test_a_change_is_audited_and_a_repeat_is_not(self, rt):
        switch(rt, True)
        switch(rt, True)

        changes = [
            e
            for e in project_events(rt.harness, rt.project_id)
            if e.entry.action == "project.view_first_changed"
        ]
        assert len(changes) == 1
        assert changes[0].entry.target_id == rt.project_id


class TestAnswering:
    def test_nothing_is_recorded_before_the_member_answers(self, rt):
        run_id = asked(rt)

        response = rt.owner.get(leaning_path(run_id))

        assert response.status_code == 200
        assert response.json() is None

    def test_a_member_records_the_option_they_lean_towards(self, rt):
        run_id = asked(rt)

        response = answer(rt, run_id, CASH)

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["option"] == CASH
        assert body["answered_at"]
        assert rt.owner.get(leaning_path(run_id)).json()["option"] == CASH

    def test_not_sure_is_recorded_as_no_option(self, rt):
        run_id = asked(rt)

        response = answer(rt, run_id, None)

        assert response.status_code == 200
        assert response.json()["option"] is None
        assert rt.owner.get(leaning_path(run_id)).json()["option"] is None

    @pytest.mark.parametrize("role", ["reviewer", "viewer"])
    def test_every_member_may_answer_for_themselves(self, rt, role):
        run_id = asked(rt)

        response = answer(rt, run_id, CASH, browser=rt.team.browser_for(role))

        assert response.status_code == 200

    def test_only_the_member_who_answered_sees_their_answer(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, CASH, browser=rt.viewer)

        assert rt.owner.get(leaning_path(run_id)).json() is None
        assert rt.reviewer.get(leaning_path(run_id)).json() is None
        assert rt.viewer.get(leaning_path(run_id)).json()["option"] == CASH

    def test_an_option_the_brief_does_not_offer_is_refused(self, rt):
        run_id = asked(rt)

        response = answer(rt, run_id, "Vouchers")

        assert response.status_code == 422
        assert rt.owner.get(leaning_path(run_id)).json() is None

    def test_a_second_answer_is_refused_and_the_first_stays(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, CASH)

        response = answer(rt, run_id, "In-kind food")

        assert response.status_code == 409
        assert rt.owner.get(leaning_path(run_id)).json()["option"] == CASH

    def test_a_project_that_does_not_ask_refuses_an_answer(self, rt):
        run_id = write_brief(rt)

        response = answer(rt, run_id, CASH)

        assert response.status_code == 409
        assert rt.owner.get(leaning_path(run_id)).json() is None

    def test_a_run_without_a_brief_has_nothing_to_lean_on(self, rt):
        switch(rt, True)
        run_id = rt.start_run(["answer"])

        assert answer(rt, run_id, CASH).status_code == 404

    def test_a_non_member_gets_the_same_404_as_for_a_missing_run(self, rt):
        run_id = asked(rt)
        missing = "00000000-0000-4000-8000-000000000000"

        mine = rt.outsider.get(leaning_path(run_id))
        absent = rt.outsider.get(leaning_path(missing))

        assert mine.status_code == 404
        assert (mine.status_code, mine.json()) == (absent.status_code, absent.json())
        assert answer(rt, run_id, CASH, browser=rt.outsider).status_code == 404


class TestStoredSafely:
    def test_the_answer_is_sealed_under_the_projects_key_bound_to_its_row(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, CASH)
        record = stored(rt, run_id, OWNER_EMAIL)
        assert record is not None
        assert CASH.encode() not in record.option_sealed

        async def decrypt(row: str) -> bytes:
            providers = rt.harness.providers
            async with providers.storage.unit_of_work() as uow:
                context = EncryptionContext(UUID(rt.project_id), "leanings.option", row)
                return await providers.encryption.decrypt(uow, context, record.option_sealed)

        assert CASH.encode() in asyncio.run(decrypt(f"{run_id}:{record.user_id}"))
        with pytest.raises(DecryptionError):
            asyncio.run(decrypt(f"{run_id}:{rt.team.user_id(OWNER_EMAIL)}-other"))

    def test_the_answer_is_unreadable_once_the_project_is_deleted(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, CASH)
        record = stored(rt, run_id, OWNER_EMAIL)
        assert record is not None

        assert send(rt.owner, "DELETE", f"{PROJECTS}/{rt.project_id}").status_code == 204

        async def decrypt() -> bytes:
            providers = rt.harness.providers
            async with providers.storage.unit_of_work() as uow:
                context = EncryptionContext(
                    UUID(rt.project_id),
                    "leanings.option",
                    f"{run_id}:{record.user_id}",
                )
                return await providers.encryption.decrypt(uow, context, record.option_sealed)

        with pytest.raises(KeyDestroyedError):
            asyncio.run(decrypt())


class TestAudit:
    def test_an_answer_is_audited_with_ids_only(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, CASH)

        events = project_events(rt.harness, rt.project_id)
        recorded = [e for e in events if e.entry.action == "brief.leaning_recorded"]

        assert len(recorded) == 1
        assert (recorded[0].entry.target_type, recorded[0].entry.target_id) == ("run", run_id)
        assert "Cash" not in repr(events)

    def test_a_refused_answer_is_not_audited(self, rt):
        run_id = asked(rt)
        answer(rt, run_id, "Vouchers")

        actions = [e.entry.action for e in project_events(rt.harness, rt.project_id)]

        assert "brief.leaning_recorded" not in actions
