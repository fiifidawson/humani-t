"""In-memory storage for tests and for running the API without a database.

Transactions are real: changes are staged on a copy and only replace the shared
state on `commit`, so a test that forgets to commit fails the same way it would
against Postgres. Concurrent transactions are not isolated from each other —
last commit wins — which is acceptable for tests and single-user development.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from app.domain.accounts import Invitation, PasswordReset, User
from app.domain.audit import GENESIS, AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.domain.sessions import Session


class _MemoryState:
    def __init__(self) -> None:
        self.data_keys: dict[UUID, WrappedDataKey] = {}
        self.audit_events: list[AuditEvent] = []
        self.audit_head: ChainHead = GENESIS
        self.users: dict[UUID, User] = {}
        self.sessions: dict[UUID, Session] = {}
        self.invitations: dict[UUID, Invitation] = {}
        self.password_resets: dict[UUID, PasswordReset] = {}

    def copy(self) -> _MemoryState:
        clone = _MemoryState()
        clone.data_keys = dict(self.data_keys)
        clone.audit_events = list(self.audit_events)
        clone.audit_head = self.audit_head
        clone.users = dict(self.users)
        clone.sessions = dict(self.sessions)
        clone.invitations = dict(self.invitations)
        clone.password_resets = dict(self.password_resets)
        return clone


def _insert[K, V](table: dict[K, V], key: K, value: V, what: str) -> None:
    if key in table:
        raise ValueError(f"{what} {key} already exists")
    table[key] = value


def _update[K, V](table: dict[K, V], key: K, value: V, what: str) -> None:
    if key not in table:
        raise ValueError(f"{what} {key} does not exist")
    table[key] = value


class MemoryDataKeyRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        return self._state.data_keys.get(scope_id)

    async def add(self, key: WrappedDataKey) -> None:
        _insert(self._state.data_keys, key.scope_id, key, "data key for scope")

    async def save(self, key: WrappedDataKey) -> None:
        _update(self._state.data_keys, key.scope_id, key, "data key for scope")

    async def list_live(self) -> list[WrappedDataKey]:
        return [k for k in self._state.data_keys.values() if not k.is_destroyed]


class MemoryAuditRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def lock_head(self) -> ChainHead:
        return self._state.audit_head

    async def append(self, event: AuditEvent) -> None:
        head = self._state.audit_head
        if event.sequence != head.sequence + 1 or event.previous_hash != head.hash:
            raise ValueError("audit event does not extend the current chain head")
        self._state.audit_events.append(event)
        self._state.audit_head = ChainHead(event.sequence, event.hash)

    async def head(self) -> ChainHead:
        return self._state.audit_head

    async def list_events(self) -> list[AuditEvent]:
        return list(self._state.audit_events)


class MemoryUserRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, user_id: UUID) -> User | None:
        return self._state.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self._state.users.values() if u.email == email), None)

    async def add(self, user: User) -> None:
        if await self.get_by_email(user.email) is not None:
            raise ValueError("a user with this email already exists")
        _insert(self._state.users, user.id, user, "user")

    async def save(self, user: User) -> None:
        _update(self._state.users, user.id, user, "user")


class MemorySessionRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, session_id: UUID) -> Session | None:
        return self._state.sessions.get(session_id)

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        return next(
            (s for s in self._state.sessions.values() if s.token_hash == token_hash), None
        )

    async def add(self, session: Session) -> None:
        _insert(self._state.sessions, session.id, session, "session")

    async def save(self, session: Session) -> None:
        _update(self._state.sessions, session.id, session, "session")

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        return [
            s
            for s in self._state.sessions.values()
            if s.user_id == user_id and s.revoked_at is None
        ]


class MemoryInvitationRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None:
        return next(
            (i for i in self._state.invitations.values() if i.token_hash == token_hash), None
        )

    async def add(self, invitation: Invitation) -> None:
        _insert(self._state.invitations, invitation.id, invitation, "invitation")

    async def save(self, invitation: Invitation) -> None:
        _update(self._state.invitations, invitation.id, invitation, "invitation")


class MemoryPasswordResetRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None:
        return next(
            (r for r in self._state.password_resets.values() if r.token_hash == token_hash),
            None,
        )

    async def list_unused_for_user(self, user_id: UUID) -> list[PasswordReset]:
        return [
            r
            for r in self._state.password_resets.values()
            if r.user_id == user_id and r.used_at is None
        ]

    async def add(self, reset: PasswordReset) -> None:
        _insert(self._state.password_resets, reset.id, reset, "password reset")

    async def save(self, reset: PasswordReset) -> None:
        _update(self._state.password_resets, reset.id, reset, "password reset")


class MemoryUnitOfWork:
    def __init__(self, storage: MemoryStorage) -> None:
        self._storage = storage
        self._stage(storage.state.copy())

    def _stage(self, state: _MemoryState) -> None:
        self._staged = state
        self.data_keys = MemoryDataKeyRepository(state)
        self.audit = MemoryAuditRepository(state)
        self.users = MemoryUserRepository(state)
        self.sessions = MemorySessionRepository(state)
        self.invitations = MemoryInvitationRepository(state)
        self.password_resets = MemoryPasswordResetRepository(state)

    async def commit(self) -> None:
        self._storage.state = self._staged
        self._stage(self._staged.copy())

    async def rollback(self) -> None:
        self._stage(self._storage.state.copy())


class MemoryStorage:
    def __init__(self) -> None:
        self.state = _MemoryState()

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[MemoryUnitOfWork]:
        yield MemoryUnitOfWork(self)

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None
