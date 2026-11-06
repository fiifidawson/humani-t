"""Development on one machine: in-memory storage, the run worker in the API, the demo stand-ins.

With in-memory storage a separate worker process has its own, empty memory and
never sees a queued run, so the API works runs itself (`run_worker_in_process`).
These specs enter the app's lifespan, as uvicorn does, and drive it only
through HTTP: no test calls a worker by hand here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.domain.mfa import MfaPolicy
from app.domain.runs import STAGES
from tests.project_support import OWNER_EMAIL, PASSWORD, create_project, send, signed_in
from tests.run_support import Event, create_review, event_context
from tests.support import AuthHarness, login, make_harness

_WAIT_SECONDS = 30.0
_VOCABULARY = "[intervention]\ncash transfer; voucher\n[outcome]\nmalnutrition"
_CRITERIA = [
    {"kind": "inclusion", "text": "Displaced people or refugees"},
    {"kind": "inclusion", "text": "Measures malnutrition"},
    {"kind": "exclusion", "text": "Not a humanitarian or crisis setting"},
]


@dataclass
class Live:
    """An app whose lifespan is running, and an owner signed in to a project with a review."""

    harness: AuthHarness
    owner: TestClient
    project_id: str
    review_id: str

    def events(self, run_id: str) -> list[Event]:
        async def read() -> list[Event]:
            providers = self.harness.providers
            async with providers.storage.unit_of_work() as uow:
                stored = await uow.run_events.list_after(UUID(run_id), 0, limit=10_000)
                return [
                    Event(
                        e.id,
                        e.name.value,
                        json.loads(
                            await providers.encryption.decrypt(
                                uow,
                                event_context(self.project_id, run_id, e.id),
                                e.payload_sealed,
                            )
                        ),
                    )
                    for e in stored
                ]

        return asyncio.run(read())

    def wait_for(self, run_id: str, name: str) -> list[Event]:
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline:
            events = self.events(run_id)
            if any(e.name == name for e in events):
                return events
            time.sleep(0.05)
        raise AssertionError(f"no {name} within {_WAIT_SECONDS} s: {self.events(run_id)}")

    def start_run(self, body: dict[str, Any]) -> str:
        response = send(self.owner, "POST", f"/api/v1/reviews/{self.review_id}/runs", body)
        assert response.status_code == 202, response.text
        run_id: str = response.json()["run_id"]
        return run_id

    def accept_own_terms(self) -> None:
        plan = f"/api/v1/reviews/{self.review_id}/plan"
        body = {"format": "text", "content": _VOCABULARY, "criteria": _CRITERIA}
        assert send(self.owner, "PUT", f"{plan}/vocabulary", body).status_code == 200
        assert send(self.owner, "POST", f"{plan}/accept", {"version": 1}).status_code == 200


def _live(*, run_worker_in_process: bool | None = None) -> Iterator[Live]:
    harness = make_harness(
        mfa_policy=MfaPolicy(is_required=False),
        demo_inference=True,
        paper_sources="demo",
        run_worker_in_process=run_worker_in_process,
    )
    harness.create_user(OWNER_EMAIL, PASSWORD)
    with TestClient(harness.app):
        owner = signed_in(harness, OWNER_EMAIL)
        project_id = create_project(owner)["id"]
        review_id = create_review(owner, project_id)["id"]
        yield Live(harness, owner, project_id, review_id)


@pytest.fixture
def live() -> Iterator[Live]:
    yield from _live()


class TestInProcessWorker:
    def test_a_started_run_reaches_its_first_stage_without_a_separate_worker(self, live):
        live.accept_own_terms()

        run_id = live.start_run({"stages": ["queries"]})

        events = live.wait_for(run_id, "job.finished")
        assert events[0].name == "stage.started"
        assert events[0].data["stage"] == "queries"
        assert events[-1].data["outcome"] == "done"

    def test_switched_off_a_run_waits_for_a_worker(self):
        for live in _live(run_worker_in_process=False):
            live.accept_own_terms()

            run_id = live.start_run({"stages": ["queries"]})
            time.sleep(1)

            assert live.events(run_id) == []


class TestDemoFlow:
    def test_a_whole_review_runs_on_the_demo_model_and_sources(self, live):
        plan = f"/api/v1/reviews/{live.review_id}/plan"
        drafted = send(live.owner, "POST", plan, {"mode": "shallow"})
        assert drafted.status_code == 201, drafted.text
        version = drafted.json()["current"]["version"]
        assert (
            send(live.owner, "POST", f"{plan}/accept", {"version": version}).status_code == 200
        )

        run_id = live.start_run({})

        events = live.wait_for(run_id, "job.finished")
        assert events[-1].data == {**events[-1].data, "outcome": "done", "error": None}
        finished = [e.data["stage"] for e in events if e.name == "stage.finished"]
        assert finished == list(STAGES)
        papers = live.owner.get(f"/api/v1/reviews/{live.review_id}/papers").json()
        assert papers["counts"]["skipped"] > 0
        assert any(p["decision"] for p in papers["papers"])
        assert live.owner.get(f"/api/v1/reviews/{live.review_id}/brief").status_code == 200

    def test_what_the_demo_model_made_says_so(self, live):
        drafted = send(
            live.owner, "POST", f"/api/v1/reviews/{live.review_id}/plan", {"mode": "shallow"}
        )

        assert "not a model" in drafted.json()["receipts"][0]["model_id"]


class TestDevelopmentAdministrator:
    def test_the_administrator_is_there_when_the_app_starts(self):
        harness = make_harness(
            dev_admin_email="dev@example.org", dev_admin_password="a-long-dev-only-passphrase"
        )
        with TestClient(harness.app):
            response = login(harness.browser(), "dev@example.org", "a-long-dev-only-passphrase")

        assert response.status_code == 200
        assert response.json()["status"] == "mfa_enrolment_required"
        assert response.json()["user"]["is_admin"] is True

    def test_starting_twice_in_one_process_keeps_the_one_account(self):
        harness = make_harness(
            dev_admin_email="dev@example.org", dev_admin_password="a-long-dev-only-passphrase"
        )
        with TestClient(harness.app):
            pass
        with TestClient(harness.app):
            response = login(harness.browser(), "dev@example.org", "a-long-dev-only-passphrase")

        assert response.status_code == 200
