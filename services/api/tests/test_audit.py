"""The audit log: what it records, and that tampering is always detected."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest

from app.application.audit import SYSTEM, Actor, AuditLog
from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider
from app.domain.audit import GENESIS, AuditEntry, AuditEvent, verify_chain
from app.domain.encryption import KeyDestroyedError
from app.storage.memory import MemoryStorage

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _entry(action: str = "project.created", **overrides: object) -> AuditEntry:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "occurred_at": NOW,
        "action": action,
        "actor_id": uuid.uuid4(),
        "target_type": "project",
        "target_id": str(uuid.uuid4()),
    }
    values.update(overrides)
    return AuditEntry(**values)  # type: ignore[arg-type]


def _chain(length: int) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    head = GENESIS
    for _ in range(length):
        event = AuditEvent.chain(_entry(), head.sequence + 1, head.hash)
        events.append(event)
        head = dataclasses.replace(head, sequence=event.sequence, hash=event.hash)
    return events


class TestAuditEntry:
    @pytest.mark.parametrize("action", ["deleted", "Project.Deleted", "project.", ".deleted"])
    def test_actions_must_be_noun_dot_verb(self, action):
        with pytest.raises(ValueError, match=r"noun.verb"):
            _entry(action)

    def test_details_without_a_scope_are_refused(self):
        with pytest.raises(ValueError, match="scope_id"):
            _entry(details_sealed=b"x")

    def test_the_same_entry_always_hashes_the_same(self):
        entry = _entry()

        assert entry.canonical(1) == entry.canonical(1)
        assert entry.canonical(1) != entry.canonical(2)


class TestVerifyChain:
    def test_an_untouched_chain_is_intact(self):
        events = _chain(5)

        report = verify_chain(events)

        assert report.is_intact
        assert report.events_checked == 5

    def test_an_edited_event_is_detected(self):
        events = _chain(5)
        forged = dataclasses.replace(events[2].entry, action="project.viewed")
        events[2] = dataclasses.replace(events[2], entry=forged)

        report = verify_chain(events)

        assert report.first_broken_sequence == 3
        assert report.reason == "event content was altered"

    def test_an_edit_that_recomputes_its_own_hash_still_breaks_the_next_link(self):
        events = _chain(5)
        forged = dataclasses.replace(events[2].entry, action="project.viewed")
        events[2] = AuditEvent.chain(forged, 3, events[2].previous_hash)

        assert verify_chain(events).first_broken_sequence == 4

    def test_a_deleted_event_is_detected(self):
        events = _chain(5)
        del events[1]

        assert verify_chain(events).first_broken_sequence == 2

    def test_reordered_events_are_detected(self):
        events = _chain(5)
        events[1], events[2] = events[2], events[1]

        assert not verify_chain(events).is_intact

    def test_events_deleted_from_the_end_are_caught_by_the_head(self):
        events = _chain(5)
        head = dataclasses.replace(GENESIS, sequence=5, hash=events[-1].hash)

        report = verify_chain(events[:3], head)

        assert report.reason == "events missing from the end"


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def encryption() -> ScopeEncryption:
    return ScopeEncryption(LocalKeyProvider({1: bytes(32)}), AesGcmCipher())


@pytest.fixture
def audit(encryption: ScopeEncryption) -> AuditLog:
    return AuditLog(encryption, clock=lambda: NOW)


class TestAuditLog:
    async def test_events_chain_in_order(self, storage, audit):
        async with storage.unit_of_work() as uow:
            first = await audit.record(uow, SYSTEM, "keys.rewrapped", "kek", "2")
            second = await audit.record(uow, SYSTEM, "keys.rewrapped", "kek", "3")
            await uow.commit()

        assert (first.sequence, second.sequence) == (1, 2)
        assert second.previous_hash == first.hash
        async with storage.unit_of_work() as uow:
            assert (await audit.verify(uow)).is_intact

    async def test_an_event_rolls_back_with_its_action(self, storage, audit):
        async with storage.unit_of_work() as uow:
            await audit.record(uow, SYSTEM, "keys.rewrapped", "kek", "2")

        async with storage.unit_of_work() as uow:
            assert await uow.audit.list_events() == []

    async def test_actor_and_request_are_recorded(self, storage, audit):
        user_id = uuid.uuid4()
        async with storage.unit_of_work() as uow:
            event = await audit.record(
                uow, Actor(user_id, "req-123"), "project.created", "project", "p1"
            )

        assert event.entry.actor_id == user_id
        assert event.entry.request_id == "req-123"

    async def test_details_are_encrypted_and_readable_by_the_project(
        self, storage, encryption, audit
    ):
        scope = uuid.uuid4()
        async with storage.unit_of_work() as uow:
            await encryption.create_scope(uow, scope)
            event = await audit.record(
                uow,
                SYSTEM,
                "project.renamed",
                "project",
                str(scope),
                scope_id=scope,
                details={"old_name": "Cholera response, Sana'a"},
            )

            assert event.entry.details_sealed is not None
            assert b"Cholera" not in event.entry.details_sealed
            assert await audit.read_details(uow, event) == {
                "old_name": "Cholera response, Sana'a"
            }

    async def test_a_shredded_project_keeps_a_verifiable_but_unreadable_history(
        self, storage, encryption, audit
    ):
        scope = uuid.uuid4()
        async with storage.unit_of_work() as uow:
            await encryption.create_scope(uow, scope)
            event = await audit.record(
                uow,
                SYSTEM,
                "project.renamed",
                "project",
                str(scope),
                scope_id=scope,
                details={"old_name": "secret"},
            )
            await encryption.destroy_scope(uow, scope)
            await audit.record(uow, SYSTEM, "project.deleted", "project", str(scope))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert (await audit.verify(uow)).is_intact
            with pytest.raises(KeyDestroyedError):
                await audit.read_details(uow, event)

    async def test_details_without_a_scope_are_refused(self, storage, audit):
        async with storage.unit_of_work() as uow:
            with pytest.raises(ValueError, match="scope_id"):
                await audit.record(uow, SYSTEM, "a.b", "t", "1", details={"x": 1})

    async def test_an_append_that_skips_the_head_is_refused(self, storage):
        async with storage.unit_of_work() as uow:
            stray = AuditEvent.chain(_entry(), 7, bytes(32))
            with pytest.raises(ValueError, match="chain head"):
                await uow.audit.append(stray)
