"""Persistence as repositories behind a unit of work.

No SQL and no SQLAlchemy types may appear above this package. Use cases talk to
these Protocols, are tested against `memory.py`, and run against `postgres/`.
A shared contract suite (`tests/test_storage_contract.py`) runs against both, so
the in-memory version cannot drift from the real one.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.accounts import Invitation, PasswordReset, User
from app.domain.audit import AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.domain.sessions import Session, SessionPolicy


class DataKeyRepository(Protocol):
    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        """Return `None` when the scope has no key, destroyed keys included as rows."""
        ...

    async def add(self, key: WrappedDataKey, /) -> None: ...

    async def save(self, key: WrappedDataKey, /) -> None:
        """Persist a changed key: rewrapped or destroyed."""
        ...

    async def list_live(self) -> list[WrappedDataKey]: ...


class AuditRepository(Protocol):
    async def lock_head(self) -> ChainHead:
        """Return the chain head and hold it until the transaction ends.

        Appends are serialised through this lock; without it two writers could
        both chain onto the same event and fork the log.
        """
        ...

    async def append(self, event: AuditEvent) -> None:
        """Insert the event and move the head to it. Requires `lock_head` first."""
        ...

    async def head(self) -> ChainHead: ...

    async def list_events(self) -> list[AuditEvent]:
        """All events in sequence order."""
        ...


# Writes that a concurrent request could race with are never blind overwrites.
# They are either conditional (`claim`, `revoke`: "only if still unused") and
# report whether they won, or version-checked (`UserRepository.save`). Records
# are passed positional-only (`/`) so implementations may name them freely.
class UserRepository(Protocol):
    async def get(self, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """`email` must already be normalised (`normalize_email`)."""
        ...

    async def add(self, user: User, /) -> None:
        """Raise `EmailTakenError` if the email is already registered."""
        ...

    async def save(self, user: User, /) -> None:
        """Store `user` if its `version` is current; otherwise raise `ConcurrentUpdateError`."""
        ...


class SessionRepository(Protocol):
    async def get(self, session_id: UUID) -> Session | None: ...

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None: ...

    async def add(self, session: Session, /) -> None: ...

    async def touch(self, session_id: UUID, at: datetime) -> None:
        """Record activity. Never affects a revoked session."""
        ...

    async def mark_reauthenticated(self, session_id: UUID, at: datetime) -> bool:
        """False if the session was revoked meanwhile."""
        ...

    async def revoke(self, session_id: UUID, at: datetime) -> bool:
        """False if it was already revoked."""
        ...

    async def revoke_all_for_user(
        self, user_id: UUID, at: datetime, *, keep: UUID | None = None
    ) -> int:
        """Revoke every live session of the user except `keep`, in one operation."""
        ...

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        """Unrevoked sessions, which may still include expired ones."""
        ...

    async def delete_ended(self, now: datetime, policy: SessionPolicy) -> int:
        """Delete revoked and expired sessions; return how many."""
        ...


class InvitationRepository(Protocol):
    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None: ...

    async def add(self, invitation: Invitation, /) -> None: ...

    async def claim(self, invitation_id: UUID, at: datetime) -> bool:
        """Mark accepted if still unaccepted; True only for the one caller that won."""
        ...

    async def delete_spent(self, now: datetime) -> int:
        """Delete accepted and expired invitations; return how many."""
        ...


class PasswordResetRepository(Protocol):
    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None: ...

    async def add(self, reset: PasswordReset, /) -> None: ...

    async def claim(self, reset_id: UUID, at: datetime) -> bool:
        """Mark used if still unused; True only for the one caller that won."""
        ...

    async def cancel_unused_for_user(self, user_id: UUID, at: datetime) -> int:
        """Mark every unused link of the user as used, so only a newer one works."""
        ...

    async def delete_spent(self, now: datetime) -> int:
        """Delete used and expired links; return how many."""
        ...


class UnitOfWork(Protocol):
    """One transaction. Nothing is visible to others until `commit`."""

    # Read-only properties rather than attributes: callers use repositories,
    # they never replace them, and that lets each implementation return its own.
    @property
    def data_keys(self) -> DataKeyRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def password_resets(self) -> PasswordResetRepository: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class StorageProvider(Protocol):
    def unit_of_work(self) -> AbstractAsyncContextManager[UnitOfWork]:
        """Open a transaction. Leaving the block without `commit` rolls it back.

        Raises `StorageUnavailableError` when no connection is available in time.
        Keep the block short: it holds a pooled connection for its whole length.
        """
        ...

    async def is_ready(self) -> bool:
        """Whether the backing store answers. For the readiness probe only."""
        ...

    async def close(self) -> None: ...
