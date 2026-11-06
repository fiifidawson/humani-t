"""PostgreSQL storage via SQLAlchemy 2 (async) and asyncpg."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Table, and_, delete, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.domain.accounts import EmailTakenError, Invitation, PasswordReset, User
from app.domain.audit import AuditEntry, AuditEvent, ChainHead
from app.domain.encryption import WrappedDataKey
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.domain.mfa import (
    ChallengePurpose,
    Passkey,
    PasskeyAlreadyRegisteredError,
    RecoveryCode,
    TotpAlreadyEnrolledError,
    TotpCredential,
    WebAuthnChallenge,
)
from app.domain.projects import AlreadyMemberError, Membership, Project, Role
from app.domain.sessions import Session, SessionPolicy
from app.storage.postgres.briefs import PostgresDecisionBriefRepository
from app.storage.postgres.documents import PostgresDocumentRepository
from app.storage.postgres.masking import (
    PostgresMaskingRuleRepository,
    PostgresPaperMaskRepository,
)
from app.storage.postgres.papers import PostgresPaperRepository, PostgresScreeningRepository
from app.storage.postgres.plans import PostgresPlanRepository
from app.storage.postgres.questions import PostgresQuestionRepository
from app.storage.postgres.runs import (
    PostgresGateRepository,
    PostgresReviewRepository,
    PostgresRunEventRepository,
    PostgresRunRepository,
)
from app.storage.postgres.tables import (
    audit_chain_head,
    audit_events,
    data_keys,
    invitations,
    memberships,
    passkeys,
    password_resets,
    projects,
    recovery_codes,
    sessions,
    totp_credentials,
    users,
    webauthn_challenges,
)


def _to_data_key(row: Row[Any]) -> WrappedDataKey:
    return WrappedDataKey(
        scope_id=row.scope_id,
        kek_version=row.kek_version,
        wrapped=row.wrapped,
        created_at=row.created_at,
        destroyed_at=row.destroyed_at,
    )


class PostgresDataKeyRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        result = await self._connection.execute(
            select(data_keys).where(data_keys.c.scope_id == scope_id)
        )
        row = result.one_or_none()
        return None if row is None else _to_data_key(row)

    async def add(self, key: WrappedDataKey) -> None:
        await self._connection.execute(
            insert(data_keys).values(
                scope_id=key.scope_id,
                kek_version=key.kek_version,
                wrapped=key.wrapped,
                created_at=key.created_at,
                destroyed_at=key.destroyed_at,
            )
        )

    async def save(self, key: WrappedDataKey) -> None:
        # `wrapped IS NOT NULL`: a destroyed key is final. A rewrap that read the
        # key just before a project was shredded must fail, not revive it.
        result = await self._connection.execute(
            update(data_keys)
            .where(data_keys.c.scope_id == key.scope_id, data_keys.c.wrapped.is_not(None))
            .values(
                kek_version=key.kek_version,
                wrapped=key.wrapped,
                destroyed_at=key.destroyed_at,
            )
        )
        if result.rowcount != 1:
            if await self.get(key.scope_id) is None:
                raise ValueError(f"data key for scope {key.scope_id} does not exist")
            raise ConcurrentUpdateError(f"data key for scope {key.scope_id} was destroyed")

    async def list_live(self) -> list[WrappedDataKey]:
        result = await self._connection.execute(
            select(data_keys).where(data_keys.c.wrapped.is_not(None))
        )
        return [_to_data_key(row) for row in result]


def _to_audit_event(row: Row[Any]) -> AuditEvent:
    return AuditEvent(
        entry=AuditEntry(
            id=row.id,
            occurred_at=row.occurred_at,
            action=row.action,
            actor_id=row.actor_id,
            target_type=row.target_type,
            target_id=row.target_id,
            scope_id=row.scope_id,
            request_id=row.request_id,
            details_sealed=row.details_sealed,
        ),
        sequence=row.sequence,
        previous_hash=row.previous_hash,
        hash=row.hash,
    )


class PostgresAuditRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def lock_head(self) -> ChainHead:
        result = await self._connection.execute(
            select(audit_chain_head.c.sequence, audit_chain_head.c.hash)
            .where(audit_chain_head.c.id == 1)
            .with_for_update()
        )
        row = result.one()
        return ChainHead(row.sequence, row.hash)

    async def append(self, event: AuditEvent) -> None:
        entry = event.entry
        await self._connection.execute(
            insert(audit_events).values(
                sequence=event.sequence,
                id=entry.id,
                occurred_at=entry.occurred_at,
                action=entry.action,
                actor_id=entry.actor_id,
                target_type=entry.target_type,
                target_id=entry.target_id,
                scope_id=entry.scope_id,
                request_id=entry.request_id,
                details_sealed=entry.details_sealed,
                previous_hash=event.previous_hash,
                hash=event.hash,
            )
        )
        # The WHERE re-checks the head the event was chained onto, so an append
        # without the lock fails loudly instead of forking the chain.
        result = await self._connection.execute(
            update(audit_chain_head)
            .where(
                audit_chain_head.c.id == 1,
                audit_chain_head.c.sequence == event.sequence - 1,
                audit_chain_head.c.hash == event.previous_hash,
            )
            .values(sequence=event.sequence, hash=event.hash)
        )
        if result.rowcount != 1:
            raise ValueError("audit event does not extend the current chain head")

    async def head(self) -> ChainHead:
        result = await self._connection.execute(
            select(audit_chain_head.c.sequence, audit_chain_head.c.hash).where(
                audit_chain_head.c.id == 1
            )
        )
        row = result.one()
        return ChainHead(row.sequence, row.hash)

    async def list_events(self) -> list[AuditEvent]:
        result = await self._connection.execute(
            select(audit_events).order_by(audit_events.c.sequence)
        )
        return [_to_audit_event(row) for row in result]


class _RecordTable[T]:
    """Mapping for domain types whose fields match their table's columns one to one."""

    def __init__(self, connection: AsyncConnection, table: Table, record: type[T]) -> None:
        self._connection = connection
        self._table = table
        self._record = record

    def _from_row(self, row: Row[Any]) -> T:
        return self._record(**row._mapping)

    async def _one(self, **where: object) -> T | None:
        query = select(self._table).where(
            *(self._table.c[name] == value for name, value in where.items())
        )
        row = (await self._connection.execute(query)).one_or_none()
        return None if row is None else self._from_row(row)

    async def _many(self, *conditions: Any) -> list[T]:
        result = await self._connection.execute(select(self._table).where(*conditions))
        return [self._from_row(row) for row in result]

    async def add(self, record: T, /) -> None:
        await self._connection.execute(
            insert(self._table).values(**dataclasses.asdict(record))  # type: ignore[call-overload]
        )

    async def _update_where(self, *conditions: Any, **values: object) -> int:
        result = await self._connection.execute(
            update(self._table).where(*conditions).values(**values)
        )
        return result.rowcount

    async def _delete_where(self, *conditions: Any) -> int:
        result = await self._connection.execute(delete(self._table).where(*conditions))
        return result.rowcount


class PostgresUserRepository(_RecordTable[User]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, users, User)

    async def get(self, user_id: UUID) -> User | None:
        return await self._one(id=user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self._one(email=email)

    async def add(self, user: User, /) -> None:
        # A savepoint, so a duplicate rolls back only this insert and the
        # surrounding transaction stays usable.
        try:
            async with self._connection.begin_nested():
                await super().add(user)
        except IntegrityError as exc:
            raise EmailTakenError("An account with this email already exists.") from exc

    async def save(self, user: User, /) -> None:
        values = dataclasses.asdict(user)
        values["version"] = user.version + 1
        changed = await self._update_where(
            users.c.id == user.id, users.c.version == user.version, **values
        )
        if changed != 1:
            raise ConcurrentUpdateError(f"user {user.id} changed concurrently")

    async def lock_enabled_admins(self) -> list[User]:
        # In id order, so two lockers never hold part of the set each. A waiter
        # re-reads each row once it is released: one disabled meanwhile drops out.
        result = await self._connection.execute(
            select(users)
            .where(users.c.is_admin.is_(True), users.c.is_disabled.is_(False))
            .order_by(users.c.id)
            .with_for_update()
        )
        return [self._from_row(row) for row in result]


class PostgresSessionRepository(_RecordTable[Session]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, sessions, Session)

    async def get(self, session_id: UUID) -> Session | None:
        return await self._one(id=session_id)

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        return await self._one(token_hash=token_hash)

    async def touch(self, session_id: UUID, at: datetime) -> None:
        await self._update_where(
            sessions.c.id == session_id, sessions.c.revoked_at.is_(None), last_seen_at=at
        )

    async def revoke(self, session_id: UUID, at: datetime) -> bool:
        changed = await self._update_where(
            sessions.c.id == session_id, sessions.c.revoked_at.is_(None), revoked_at=at
        )
        return changed == 1

    async def revoke_all_for_user(
        self, user_id: UUID, at: datetime, *, keep: UUID | None = None
    ) -> int:
        conditions = [sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None)]
        if keep is not None:
            conditions.append(sessions.c.id != keep)
        return await self._update_where(*conditions, revoked_at=at)

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        return await self._many(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))

    async def delete_ended(self, now: datetime, policy: SessionPolicy) -> int:
        return await self._delete_where(
            or_(
                sessions.c.revoked_at.is_not(None),
                sessions.c.last_seen_at < now - policy.idle_timeout,
                sessions.c.created_at < now - policy.absolute_timeout,
                and_(
                    sessions.c.is_mfa_pending,
                    sessions.c.created_at < now - policy.pending_mfa_timeout,
                ),
            )
        )


class PostgresInvitationRepository(_RecordTable[Invitation]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, invitations, Invitation)

    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None:
        return await self._one(token_hash=token_hash)

    async def claim(self, invitation_id: UUID, at: datetime) -> bool:
        # One UPDATE with the condition inside it: Postgres row-locks the
        # invitation, so a second claimant waits, re-checks, and finds it taken.
        changed = await self._update_where(
            invitations.c.id == invitation_id,
            invitations.c.accepted_at.is_(None),
            accepted_at=at,
        )
        return changed == 1

    async def delete_spent(self, now: datetime) -> int:
        return await self._delete_where(
            or_(invitations.c.accepted_at.is_not(None), invitations.c.expires_at <= now)
        )


class PostgresPasswordResetRepository(_RecordTable[PasswordReset]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, password_resets, PasswordReset)

    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None:
        return await self._one(token_hash=token_hash)

    async def claim(self, reset_id: UUID, at: datetime) -> bool:
        changed = await self._update_where(
            password_resets.c.id == reset_id, password_resets.c.used_at.is_(None), used_at=at
        )
        return changed == 1

    async def cancel_unused_for_user(self, user_id: UUID, at: datetime) -> int:
        return await self._update_where(
            password_resets.c.user_id == user_id,
            password_resets.c.used_at.is_(None),
            used_at=at,
        )

    async def delete_spent(self, now: datetime) -> int:
        return await self._delete_where(
            or_(password_resets.c.used_at.is_not(None), password_resets.c.expires_at <= now)
        )


class PostgresProjectRepository(_RecordTable[Project]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, projects, Project)

    async def get(self, project_id: UUID) -> Project | None:
        return await self._one(id=project_id)

    async def lock(self, project_id: UUID) -> Project | None:
        row = (
            await self._connection.execute(
                select(projects).where(projects.c.id == project_id).with_for_update()
            )
        ).one_or_none()
        return None if row is None else self._from_row(row)

    async def save(self, project: Project, /) -> None:
        values = dataclasses.asdict(project)
        values["version"] = project.version + 1
        changed = await self._update_where(
            projects.c.id == project.id, projects.c.version == project.version, **values
        )
        if changed != 1:
            if await self.get(project.id) is None:
                raise ValueError(f"project {project.id} does not exist")
            raise ConcurrentUpdateError(f"project {project.id} changed concurrently")

    async def list_for_member(self, user_id: UUID) -> list[Project]:
        result = await self._connection.execute(
            select(projects)
            .join(memberships, memberships.c.project_id == projects.c.id)
            .where(memberships.c.user_id == user_id, projects.c.deleted_at.is_(None))
            .order_by(projects.c.created_at.desc(), projects.c.id.desc())
        )
        return [self._from_row(row) for row in result]


class PostgresMembershipRepository(_RecordTable[Membership]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, memberships, Membership)

    def _from_row(self, row: Row[Any]) -> Membership:
        return Membership(**{**row._mapping, "role": Role(row.role)})

    async def get(self, project_id: UUID, user_id: UUID) -> Membership | None:
        return await self._one(project_id=project_id, user_id=user_id)

    async def list_for_project(self, project_id: UUID) -> list[Membership]:
        result = await self._connection.execute(
            select(memberships)
            .where(memberships.c.project_id == project_id)
            .order_by(memberships.c.added_at, memberships.c.user_id)
        )
        return [self._from_row(row) for row in result]

    async def add(self, membership: Membership, /) -> None:
        # A savepoint, so a duplicate rolls back only this insert and the
        # surrounding transaction stays usable.
        try:
            async with self._connection.begin_nested():
                await super().add(membership)
        except IntegrityError as exc:
            if await self.get(membership.project_id, membership.user_id) is None:
                raise
            raise AlreadyMemberError("This person is already a member of the project.") from exc

    async def change_role(self, project_id: UUID, user_id: UUID, role: Role) -> bool:
        changed = await self._update_where(
            memberships.c.project_id == project_id,
            memberships.c.user_id == user_id,
            role=role.value,
        )
        return changed == 1

    async def remove(self, project_id: UUID, user_id: UUID) -> bool:
        removed = await self._delete_where(
            memberships.c.project_id == project_id, memberships.c.user_id == user_id
        )
        return removed == 1

    async def remove_all_for_project(self, project_id: UUID) -> int:
        return await self._delete_where(memberships.c.project_id == project_id)


class PostgresTotpCredentialRepository(_RecordTable[TotpCredential]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, totp_credentials, TotpCredential)

    async def get_for_user(self, user_id: UUID) -> TotpCredential | None:
        return await self._one(user_id=user_id)

    async def add(self, credential: TotpCredential, /) -> None:
        try:
            async with self._connection.begin_nested():
                await super().add(credential)
        except IntegrityError as exc:
            raise TotpAlreadyEnrolledError(
                "This account already has an authenticator app."
            ) from exc

    async def confirm(self, credential_id: UUID, at: datetime) -> bool:
        changed = await self._update_where(
            totp_credentials.c.id == credential_id,
            totp_credentials.c.confirmed_at.is_(None),
            confirmed_at=at,
        )
        return changed == 1

    async def advance_step(self, credential_id: UUID, step: int) -> bool:
        # The comparison is inside the UPDATE, so two requests carrying the same
        # code serialise on the row lock and the second finds the step taken.
        changed = await self._update_where(
            totp_credentials.c.id == credential_id,
            or_(
                totp_credentials.c.last_used_step.is_(None),
                totp_credentials.c.last_used_step < step,
            ),
            last_used_step=step,
        )
        return changed == 1

    async def delete(self, credential_id: UUID) -> bool:
        return await self._delete_where(totp_credentials.c.id == credential_id) == 1


class PostgresRecoveryCodeRepository(_RecordTable[RecoveryCode]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, recovery_codes, RecoveryCode)

    async def replace_for_user(self, user_id: UUID, codes: Sequence[RecoveryCode], /) -> None:
        await self._delete_where(recovery_codes.c.user_id == user_id)
        for code in codes:
            await self.add(code)

    async def claim(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        changed = await self._update_where(
            recovery_codes.c.user_id == user_id,
            recovery_codes.c.code_hash == code_hash,
            recovery_codes.c.used_at.is_(None),
            used_at=at,
        )
        return changed >= 1

    async def count_unused(self, user_id: UUID) -> int:
        result = await self._connection.execute(
            select(func.count()).where(
                recovery_codes.c.user_id == user_id, recovery_codes.c.used_at.is_(None)
            )
        )
        return int(result.scalar_one())


def _to_passkey(row: Row[Any]) -> Passkey:
    values = dict(row._mapping)
    values["transports"] = tuple(t for t in values["transports"].split(",") if t)
    return Passkey(**values)


class PostgresPasskeyRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def _one(self, *conditions: Any) -> Passkey | None:
        row = (
            await self._connection.execute(select(passkeys).where(*conditions))
        ).one_or_none()
        return None if row is None else _to_passkey(row)

    async def get(self, passkey_id: UUID) -> Passkey | None:
        return await self._one(passkeys.c.id == passkey_id)

    async def get_by_credential_id(self, credential_id: bytes) -> Passkey | None:
        return await self._one(passkeys.c.credential_id == credential_id)

    async def list_for_user(self, user_id: UUID) -> list[Passkey]:
        result = await self._connection.execute(
            select(passkeys)
            .where(passkeys.c.user_id == user_id)
            .order_by(passkeys.c.created_at)
        )
        return [_to_passkey(row) for row in result]

    async def add(self, passkey: Passkey, /) -> None:
        values = dataclasses.asdict(passkey)
        values["transports"] = ",".join(passkey.transports)
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(insert(passkeys).values(**values))
        except IntegrityError as exc:
            raise PasskeyAlreadyRegisteredError("This passkey is already registered.") from exc

    async def record_use(self, passkey_id: UUID, sign_count: int, at: datetime) -> bool:
        advances = passkeys.c.sign_count < sign_count
        # A zero from an authenticator that never counts is accepted only while
        # the stored count is zero too (WebAuthn §6.1.1).
        accepted = or_(advances, passkeys.c.sign_count == 0) if sign_count == 0 else advances
        result = await self._connection.execute(
            update(passkeys)
            .where(passkeys.c.id == passkey_id, accepted)
            .values(sign_count=sign_count, last_used_at=at)
        )
        return result.rowcount == 1

    async def delete(self, passkey_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(passkeys).where(passkeys.c.id == passkey_id)
        )
        return result.rowcount == 1


class PostgresWebAuthnChallengeRepository(_RecordTable[WebAuthnChallenge]):
    def __init__(self, connection: AsyncConnection) -> None:
        super().__init__(connection, webauthn_challenges, WebAuthnChallenge)

    async def take(
        self, session_id: UUID, purpose: ChallengePurpose, at: datetime
    ) -> WebAuthnChallenge | None:
        # One UPDATE ... RETURNING spends them all; a racing request blocks on
        # the row locks and then finds nothing left to spend.
        result = await self._connection.execute(
            update(webauthn_challenges)
            .where(
                webauthn_challenges.c.session_id == session_id,
                webauthn_challenges.c.purpose == purpose.value,
                webauthn_challenges.c.used_at.is_(None),
            )
            .values(used_at=at)
            .returning(*webauthn_challenges.c)
        )
        spent = [self._from_row(row) for row in result]
        newest = max(spent, key=lambda c: c.created_at, default=None)
        return newest if newest is not None and at < newest.expires_at else None

    async def delete_spent(self, now: datetime) -> int:
        return await self._delete_where(
            or_(
                webauthn_challenges.c.used_at.is_not(None),
                webauthn_challenges.c.expires_at <= now,
            )
        )


class PostgresUnitOfWork:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.data_keys = PostgresDataKeyRepository(connection)
        self.audit = PostgresAuditRepository(connection)
        self.users = PostgresUserRepository(connection)
        self.sessions = PostgresSessionRepository(connection)
        self.invitations = PostgresInvitationRepository(connection)
        self.password_resets = PostgresPasswordResetRepository(connection)
        self.totp_credentials = PostgresTotpCredentialRepository(connection)
        self.recovery_codes = PostgresRecoveryCodeRepository(connection)
        self.passkeys = PostgresPasskeyRepository(connection)
        self.webauthn_challenges = PostgresWebAuthnChallengeRepository(connection)
        self.projects = PostgresProjectRepository(connection)
        self.memberships = PostgresMembershipRepository(connection)
        self.reviews = PostgresReviewRepository(connection)
        self.runs = PostgresRunRepository(connection)
        self.run_events = PostgresRunEventRepository(connection)
        self.gates = PostgresGateRepository(connection)
        self.papers = PostgresPaperRepository(connection)
        self.screening = PostgresScreeningRepository(connection)
        self.plans = PostgresPlanRepository(connection)
        self.briefs = PostgresDecisionBriefRepository(connection)
        self.questions = PostgresQuestionRepository(connection)
        self.masking_rules = PostgresMaskingRuleRepository(connection)
        self.paper_masks = PostgresPaperMaskRepository(connection)
        self.documents = PostgresDocumentRepository(connection)

    async def commit(self) -> None:
        await self._connection.commit()

    async def rollback(self) -> None:
        await self._connection.rollback()


class PostgresStorage:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        pool_size: int = 10,
        pool_timeout_seconds: float = 5,
        statement_timeout_ms: int = 15_000,
        idle_in_transaction_timeout_ms: int = 30_000,
    ) -> PostgresStorage:
        """Build a bounded pool whose connections time out stuck work on the server.

        `pool_size` is a hard limit (no overflow), so the database sees a
        predictable number of connections. A request that cannot get one within
        `pool_timeout_seconds` fails fast with `StorageUnavailableError` instead
        of queueing behind everyone else.
        """
        return cls(
            create_async_engine(
                url,
                pool_size=pool_size,
                max_overflow=0,
                pool_timeout=pool_timeout_seconds,
                # Drops connections Postgres closed while idle, instead of failing
                # the first request after a database restart.
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args={
                    "server_settings": {
                        # Server-side backstops: a runaway query, or a transaction
                        # left open by a bug, cannot hold locks indefinitely.
                        "statement_timeout": str(statement_timeout_ms),
                        "idle_in_transaction_session_timeout": str(
                            idle_in_transaction_timeout_ms
                        ),
                    }
                },
            )
        )

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[PostgresUnitOfWork]:
        try:
            connection = await self._engine.connect()
        except (PoolTimeoutError, OperationalError, OSError) as exc:
            raise StorageUnavailableError("no database connection available") from exc
        try:
            yield PostgresUnitOfWork(connection)
        finally:
            # Uncommitted work is discarded, matching the in-memory version, and
            # the connection always goes back to the pool.
            await connection.rollback()
            await connection.close()

    async def server_settings(self, *names: str) -> dict[str, str]:
        """Current values of Postgres settings, as a pooled connection sees them."""
        async with self._engine.connect() as connection:
            return {
                name: str((await connection.execute(text(f"SHOW {name}"))).scalar_one())
                for name in names
            }

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True

    async def close(self) -> None:
        await self._engine.dispose()
