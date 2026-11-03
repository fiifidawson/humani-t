"""The audit log's rules: what an event records and how the chain proves it intact.

Each event commits to the hash of the one before it. Changing, removing or
reordering any stored event breaks every hash after it, which `verify_chain`
reports. The chain proves tampering happened; it cannot prevent it — that is the
database grants' job.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

GENESIS_HASH = bytes(32)
_ACTION = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """What happened, before it is placed in the chain.

    Holds identifiers only. Anything descriptive goes in `details_sealed`,
    encrypted with the project's key, so shredding a project makes its history
    unreadable without making the chain unverifiable.
    """

    id: UUID
    occurred_at: datetime
    action: str
    actor_id: UUID | None
    target_type: str
    target_id: str
    scope_id: UUID | None = None
    request_id: str | None = None
    details_sealed: bytes | None = None

    def __post_init__(self) -> None:
        if not _ACTION.match(self.action):
            raise ValueError(
                "AuditEntry.action must look like 'noun.verb', e.g. 'project.deleted'"
            )
        if self.occurred_at.tzinfo is None:
            raise ValueError("AuditEntry.occurred_at must be timezone-aware")
        if not self.target_type.strip() or not self.target_id.strip():
            raise ValueError("AuditEntry.target_type and target_id must not be blank")
        if self.details_sealed is not None and self.scope_id is None:
            raise ValueError("AuditEntry.details_sealed needs a scope_id to be encrypted under")

    def canonical(self, sequence: int) -> bytes:
        # Sorted keys and fixed separators: the same event must always hash the
        # same, whichever process wrote it and whichever database returned it.
        return json.dumps(
            {
                "sequence": sequence,
                "id": str(self.id),
                "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
                "action": self.action,
                "actor_id": None if self.actor_id is None else str(self.actor_id),
                "target_type": self.target_type,
                "target_id": self.target_id,
                "scope_id": None if self.scope_id is None else str(self.scope_id),
                "request_id": self.request_id,
                "details_sealed": None
                if self.details_sealed is None
                else self.details_sealed.hex(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An entry placed in the chain: its position and the hashes that bind it."""

    entry: AuditEntry
    sequence: int
    previous_hash: bytes
    hash: bytes

    @classmethod
    def chain(cls, entry: AuditEntry, sequence: int, previous_hash: bytes) -> AuditEvent:
        return cls(entry, sequence, previous_hash, chain_hash(previous_hash, entry, sequence))


@dataclass(frozen=True, slots=True)
class ChainHead:
    sequence: int
    hash: bytes


GENESIS = ChainHead(sequence=0, hash=GENESIS_HASH)


def chain_hash(previous_hash: bytes, entry: AuditEntry, sequence: int) -> bytes:
    return hashlib.sha256(previous_hash + entry.canonical(sequence)).digest()


@dataclass(frozen=True, slots=True)
class ChainReport:
    events_checked: int
    first_broken_sequence: int | None = None
    reason: str | None = None

    @property
    def is_intact(self) -> bool:
        return self.first_broken_sequence is None


def verify_chain(events: Iterable[AuditEvent], head: ChainHead | None = None) -> ChainReport:
    """Check sequence, links and hashes, in order.

    Pass the stored `head` to also catch events deleted from the end of the log,
    which leave every remaining link intact.
    """
    expected = GENESIS
    checked = 0
    for event in events:
        checked += 1
        if event.sequence != expected.sequence + 1:
            return ChainReport(checked, expected.sequence + 1, "sequence gap or reordering")
        if event.previous_hash != expected.hash:
            return ChainReport(checked, event.sequence, "link to the previous event is broken")
        if event.hash != chain_hash(event.previous_hash, event.entry, event.sequence):
            return ChainReport(checked, event.sequence, "event content was altered")
        expected = ChainHead(event.sequence, event.hash)
    if head is not None and head != expected:
        return ChainReport(checked, expected.sequence + 1, "events missing from the end")
    return ChainReport(checked)
