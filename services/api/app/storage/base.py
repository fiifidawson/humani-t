"""Persistence as repositories behind a unit of work.

No SQL and no SQLAlchemy types may appear above this package. Use cases talk to
these Protocols, are tested against `memory.py`, and run against `postgres/`.
A shared contract suite (`tests/test_storage_contract.py`) runs against both, so
the in-memory version cannot drift from the real one.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from app.domain.accounts import Invitation, PasswordReset, User
from app.domain.audit import AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.domain.sessions import Session


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


# `add` and `save` take their record positional-only (`/`): implementations may
# name the parameter as they like, and no caller can come to depend on the name.
class UserRepository(Protocol):
    async def get(self, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """`email` must already be normalised (`normalize_email`)."""
        ...

    async def add(self, user: User, /) -> None: ...

    async def save(self, user: User, /) -> None: ...


class SessionRepository(Protocol):
    async def get(self, session_id: UUID) -> Session | None: ...

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None: ...

    async def add(self, session: Session, /) -> None: ...

    async def save(self, session: Session, /) -> None: ...

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        """Unrevoked sessions, which may still include expired ones."""
        ...


class InvitationRepository(Protocol):
    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None: ...

    async def add(self, invitation: Invitation, /) -> None: ...

    async def save(self, invitation: Invitation, /) -> None: ...


class PasswordResetRepository(Protocol):
    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None: ...

    async def list_unused_for_user(self, user_id: UUID) -> list[PasswordReset]: ...

    async def add(self, reset: PasswordReset, /) -> None: ...

    async def save(self, reset: PasswordReset, /) -> None: ...


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
        """Open a transaction. Leaving the block without `commit` rolls it back."""
        ...

    async def is_ready(self) -> bool:
        """Whether the backing store answers. For the readiness probe only."""
        ...

    async def close(self) -> None: ...
