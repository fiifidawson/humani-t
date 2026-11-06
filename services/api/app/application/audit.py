"""Recording and verifying the audit log.

Events are written inside the caller's unit of work, so an action and its audit
record commit or roll back together: there is never a deletion without its
record, nor a record of a deletion that did not happen.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.encryption import ScopeEncryption
from app.domain.audit import AuditEntry, AuditEvent, ChainReport, verify_chain
from app.domain.encryption import EncryptionContext
from app.storage.base import UnitOfWork

_DETAILS_PURPOSE = "audit_events.details"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Actor:
    """Who did it. `user_id` is `None` for the system itself: the CLI, a worker job."""

    user_id: UUID | None = None
    request_id: str | None = None


SYSTEM = Actor()


class AuditLog:
    def __init__(
        self, encryption: ScopeEncryption, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._encryption = encryption
        self._clock = clock

    async def record(
        self,
        uow: UnitOfWork,
        actor: Actor,
        action: str,
        target_type: str,
        target_id: str,
        *,
        scope_id: UUID | None = None,
        details: Mapping[str, str | int | bool | None] | None = None,
    ) -> AuditEvent:
        """Append one event. `details` are encrypted under `scope_id`'s key."""
        entry_id = uuid.uuid4()
        sealed = None
        if details is not None:
            if scope_id is None:
                raise ValueError("audit details need a scope_id to be encrypted under")
            sealed = await self._encryption.encrypt(
                uow,
                EncryptionContext(scope_id, _DETAILS_PURPOSE, str(entry_id)),
                json.dumps(dict(details), sort_keys=True).encode(),
            )

        entry = AuditEntry(
            id=entry_id,
            occurred_at=self._clock(),
            action=action,
            actor_id=actor.user_id,
            target_type=target_type,
            target_id=target_id,
            scope_id=scope_id,
            request_id=actor.request_id,
            details_sealed=sealed,
        )
        head = await uow.audit.lock_head()
        event = AuditEvent.chain(entry, head.sequence + 1, head.hash)
        await uow.audit.append(event)
        return event

    async def read_details(
        self, uow: UnitOfWork, event: AuditEvent
    ) -> dict[str, object] | None:
        """Decrypt an event's details. Raises `KeyDestroyedError` once the scope is shredded."""
        entry = event.entry
        if entry.details_sealed is None or entry.scope_id is None:
            return None
        plaintext = await self._encryption.decrypt(
            uow,
            EncryptionContext(entry.scope_id, _DETAILS_PURPOSE, str(entry.id)),
            entry.details_sealed,
        )
        details: dict[str, object] = json.loads(plaintext)
        return details

    async def verify(self, uow: UnitOfWork) -> ChainReport:
        return verify_chain(await uow.audit.list_events(), await uow.audit.head())
