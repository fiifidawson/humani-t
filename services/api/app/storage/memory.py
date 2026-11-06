"""In-memory storage for tests and for running the API without a database.

Transactions are real: changes are staged on a copy and only replace the shared
state on `commit`, so a test that forgets to commit fails the same way it would
against Postgres. Concurrent transactions are not isolated from each other —
last commit wins — which is acceptable for tests and single-user development.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.accounts import EmailTakenError, Invitation, PasswordReset, User
from app.domain.audit import GENESIS, AuditEvent, ChainHead
from app.domain.discovery import PaperSet, PaperText, RunPaper, RunSource, StoredPaper
from app.domain.encryption import WrappedDataKey
from app.domain.errors import ConcurrentUpdateError
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
from app.domain.reviews import Review
from app.domain.runs import Gate, GateKind, Run, RunEvent, RunState
from app.domain.screening import StoredDecision
from app.domain.sessions import Session, SessionPolicy
from app.storage.memory_briefs import BriefState, MemoryDecisionBriefRepository
from app.storage.memory_documents import DocumentState, MemoryDocumentRepository
from app.storage.memory_masking import (
    MaskingState,
    MemoryMaskingRuleRepository,
    MemoryPaperMaskRepository,
)
from app.storage.memory_plans import MemoryPlanRepository, PlanState
from app.storage.memory_questions import MemoryQuestionRepository, QuestionState


class _MemoryState:
    def __init__(self) -> None:
        self.data_keys: dict[UUID, WrappedDataKey] = {}
        self.audit_events: list[AuditEvent] = []
        self.audit_head: ChainHead = GENESIS
        self.users: dict[UUID, User] = {}
        self.sessions: dict[UUID, Session] = {}
        self.invitations: dict[UUID, Invitation] = {}
        self.password_resets: dict[UUID, PasswordReset] = {}
        self.totp_credentials: dict[UUID, TotpCredential] = {}
        self.recovery_codes: dict[UUID, RecoveryCode] = {}
        self.passkeys: dict[UUID, Passkey] = {}
        self.webauthn_challenges: dict[UUID, WebAuthnChallenge] = {}
        self.projects: dict[UUID, Project] = {}
        self.memberships: dict[tuple[UUID, UUID], Membership] = {}
        self.reviews: dict[UUID, Review] = {}
        self.runs: dict[UUID, Run] = {}
        # Tuples, not lists: `copy` is shallow, and a list shared with the
        # committed state would let an uncommitted append leak into it.
        self.run_events: dict[UUID, tuple[RunEvent, ...]] = {}
        self.gates: dict[UUID, Gate] = {}
        self.papers: dict[UUID, StoredPaper] = {}
        self.paper_sets: dict[UUID, PaperSet] = {}
        self.run_papers: dict[UUID, tuple[RunPaper, ...]] = {}
        self.run_sources: dict[UUID, tuple[RunSource, ...]] = {}
        self.paper_texts: dict[UUID, PaperText] = {}
        self.screening_decisions: dict[tuple[UUID, UUID], StoredDecision] = {}
        self.plan_state = PlanState()
        self.brief_state = BriefState()
        self.question_state = QuestionState()
        self.masking_state = MaskingState()
        self.document_state = DocumentState()

    def copy(self) -> _MemoryState:
        clone = _MemoryState()
        clone.data_keys = dict(self.data_keys)
        clone.audit_events = list(self.audit_events)
        clone.audit_head = self.audit_head
        clone.users = dict(self.users)
        clone.sessions = dict(self.sessions)
        clone.invitations = dict(self.invitations)
        clone.password_resets = dict(self.password_resets)
        clone.totp_credentials = dict(self.totp_credentials)
        clone.recovery_codes = dict(self.recovery_codes)
        clone.passkeys = dict(self.passkeys)
        clone.webauthn_challenges = dict(self.webauthn_challenges)
        clone.projects = dict(self.projects)
        clone.memberships = dict(self.memberships)
        clone.reviews = dict(self.reviews)
        clone.runs = dict(self.runs)
        clone.run_events = dict(self.run_events)
        clone.gates = dict(self.gates)
        clone.papers = dict(self.papers)
        clone.paper_sets = dict(self.paper_sets)
        clone.run_papers = dict(self.run_papers)
        clone.run_sources = dict(self.run_sources)
        clone.paper_texts = dict(self.paper_texts)
        clone.screening_decisions = dict(self.screening_decisions)
        clone.plan_state = self.plan_state.copy()
        clone.brief_state = self.brief_state.copy()
        clone.question_state = self.question_state.copy()
        clone.masking_state = self.masking_state.copy()
        clone.document_state = self.document_state.copy()
        return clone


def _insert[K, V](table: dict[K, V], key: K, value: V, what: str) -> None:
    if key in table:
        raise ValueError(f"{what} {key} already exists")
    table[key] = value


class MemoryDataKeyRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, scope_id: UUID) -> WrappedDataKey | None:
        return self._state.data_keys.get(scope_id)

    async def add(self, key: WrappedDataKey) -> None:
        _insert(self._state.data_keys, key.scope_id, key, "data key for scope")

    async def save(self, key: WrappedDataKey) -> None:
        stored = self._state.data_keys.get(key.scope_id)
        if stored is None:
            raise ValueError(f"data key for scope {key.scope_id} does not exist")
        if stored.is_destroyed:
            raise ConcurrentUpdateError(f"data key for scope {key.scope_id} was destroyed")
        self._state.data_keys[key.scope_id] = key

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

    async def add(self, user: User, /) -> None:
        if await self.get_by_email(user.email) is not None:
            raise EmailTakenError("An account with this email already exists.")
        _insert(self._state.users, user.id, user, "user")

    async def save(self, user: User, /) -> None:
        stored = self._state.users.get(user.id)
        if stored is None:
            raise ValueError(f"user {user.id} does not exist")
        if stored.version != user.version:
            raise ConcurrentUpdateError(f"user {user.id} changed concurrently")
        self._state.users[user.id] = replace(user, version=user.version + 1)

    async def lock_enabled_admins(self) -> list[User]:
        # Nothing to lock: in-memory transactions do not interleave.
        return [u for u in self._state.users.values() if u.is_admin and not u.is_disabled]


class MemorySessionRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, session_id: UUID) -> Session | None:
        return self._state.sessions.get(session_id)

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        return next(
            (s for s in self._state.sessions.values() if s.token_hash == token_hash), None
        )

    async def add(self, session: Session, /) -> None:
        _insert(self._state.sessions, session.id, session, "session")

    def _live(self, session_id: UUID) -> Session | None:
        session = self._state.sessions.get(session_id)
        return session if session is not None and session.revoked_at is None else None

    async def touch(self, session_id: UUID, at: datetime) -> None:
        session = self._live(session_id)
        if session is not None:
            self._state.sessions[session_id] = session.touched(at)

    async def revoke(self, session_id: UUID, at: datetime) -> bool:
        session = self._live(session_id)
        if session is None:
            return False
        self._state.sessions[session_id] = session.revoked(at)
        return True

    async def revoke_all_for_user(
        self, user_id: UUID, at: datetime, *, keep: UUID | None = None
    ) -> int:
        targets = [s.id for s in await self.list_unrevoked_for_user(user_id) if s.id != keep]
        for session_id in targets:
            await self.revoke(session_id, at)
        return len(targets)

    async def list_unrevoked_for_user(self, user_id: UUID) -> list[Session]:
        return [
            s
            for s in self._state.sessions.values()
            if s.user_id == user_id and s.revoked_at is None
        ]

    async def delete_ended(self, now: datetime, policy: SessionPolicy) -> int:
        ended = [s.id for s in self._state.sessions.values() if not s.is_active(now, policy)]
        for session_id in ended:
            del self._state.sessions[session_id]
        return len(ended)


class MemoryInvitationRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_by_token_hash(self, token_hash: bytes) -> Invitation | None:
        return next(
            (i for i in self._state.invitations.values() if i.token_hash == token_hash), None
        )

    async def add(self, invitation: Invitation, /) -> None:
        _insert(self._state.invitations, invitation.id, invitation, "invitation")

    async def claim(self, invitation_id: UUID, at: datetime) -> bool:
        invitation = self._state.invitations.get(invitation_id)
        if invitation is None or invitation.accepted_at is not None:
            return False
        self._state.invitations[invitation_id] = invitation.accepted(at)
        return True

    async def delete_spent(self, now: datetime) -> int:
        spent = [i.id for i in self._state.invitations.values() if not i.is_usable(now)]
        for invitation_id in spent:
            del self._state.invitations[invitation_id]
        return len(spent)


class MemoryPasswordResetRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_by_token_hash(self, token_hash: bytes) -> PasswordReset | None:
        return next(
            (r for r in self._state.password_resets.values() if r.token_hash == token_hash),
            None,
        )

    async def add(self, reset: PasswordReset, /) -> None:
        _insert(self._state.password_resets, reset.id, reset, "password reset")

    async def claim(self, reset_id: UUID, at: datetime) -> bool:
        reset = self._state.password_resets.get(reset_id)
        if reset is None or reset.used_at is not None:
            return False
        self._state.password_resets[reset_id] = reset.used(at)
        return True

    async def cancel_unused_for_user(self, user_id: UUID, at: datetime) -> int:
        unused = [
            r.id
            for r in self._state.password_resets.values()
            if r.user_id == user_id and r.used_at is None
        ]
        for reset_id in unused:
            await self.claim(reset_id, at)
        return len(unused)

    async def delete_spent(self, now: datetime) -> int:
        spent = [r.id for r in self._state.password_resets.values() if not r.is_usable(now)]
        for reset_id in spent:
            del self._state.password_resets[reset_id]
        return len(spent)


class MemoryTotpCredentialRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_for_user(self, user_id: UUID) -> TotpCredential | None:
        return next(
            (c for c in self._state.totp_credentials.values() if c.user_id == user_id), None
        )

    async def add(self, credential: TotpCredential, /) -> None:
        if await self.get_for_user(credential.user_id) is not None:
            raise TotpAlreadyEnrolledError("This account already has an authenticator app.")
        _insert(self._state.totp_credentials, credential.id, credential, "TOTP credential")

    async def confirm(self, credential_id: UUID, at: datetime) -> bool:
        credential = self._state.totp_credentials.get(credential_id)
        if credential is None or credential.is_confirmed:
            return False
        self._state.totp_credentials[credential_id] = credential.confirmed(at)
        return True

    async def advance_step(self, credential_id: UUID, step: int) -> bool:
        credential = self._state.totp_credentials.get(credential_id)
        if credential is None:
            return False
        if credential.last_used_step is not None and step <= credential.last_used_step:
            return False
        self._state.totp_credentials[credential_id] = replace(credential, last_used_step=step)
        return True

    async def delete(self, credential_id: UUID) -> bool:
        return self._state.totp_credentials.pop(credential_id, None) is not None


class MemoryRecoveryCodeRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def replace_for_user(self, user_id: UUID, codes: Sequence[RecoveryCode], /) -> None:
        self._state.recovery_codes = {
            k: c for k, c in self._state.recovery_codes.items() if c.user_id != user_id
        }
        for code in codes:
            _insert(self._state.recovery_codes, code.id, code, "recovery code")

    async def claim(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        code = next(
            (
                c
                for c in self._state.recovery_codes.values()
                if c.user_id == user_id and c.code_hash == code_hash and c.is_usable
            ),
            None,
        )
        if code is None:
            return False
        self._state.recovery_codes[code.id] = code.used(at)
        return True

    async def count_unused(self, user_id: UUID) -> int:
        return sum(
            1
            for c in self._state.recovery_codes.values()
            if c.user_id == user_id and c.is_usable
        )


class MemoryPasskeyRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, passkey_id: UUID) -> Passkey | None:
        return self._state.passkeys.get(passkey_id)

    async def get_by_credential_id(self, credential_id: bytes) -> Passkey | None:
        return next(
            (p for p in self._state.passkeys.values() if p.credential_id == credential_id), None
        )

    async def list_for_user(self, user_id: UUID) -> list[Passkey]:
        owned = [p for p in self._state.passkeys.values() if p.user_id == user_id]
        return sorted(owned, key=lambda p: p.created_at)

    async def add(self, passkey: Passkey, /) -> None:
        if await self.get_by_credential_id(passkey.credential_id) is not None:
            raise PasskeyAlreadyRegisteredError("This passkey is already registered.")
        _insert(self._state.passkeys, passkey.id, passkey, "passkey")

    async def record_use(self, passkey_id: UUID, sign_count: int, at: datetime) -> bool:
        passkey = self._state.passkeys.get(passkey_id)
        if passkey is None or not passkey.accepts_sign_count(sign_count):
            return False
        self._state.passkeys[passkey_id] = replace(
            passkey, sign_count=sign_count, last_used_at=at
        )
        return True

    async def delete(self, passkey_id: UUID) -> bool:
        return self._state.passkeys.pop(passkey_id, None) is not None


class MemoryWebAuthnChallengeRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def add(self, challenge: WebAuthnChallenge, /) -> None:
        _insert(self._state.webauthn_challenges, challenge.id, challenge, "challenge")

    async def take(
        self, session_id: UUID, purpose: ChallengePurpose, at: datetime
    ) -> WebAuthnChallenge | None:
        outstanding = [
            c
            for c in self._state.webauthn_challenges.values()
            if c.session_id == session_id and c.purpose == purpose and c.used_at is None
        ]
        for challenge in outstanding:
            self._state.webauthn_challenges[challenge.id] = challenge.used(at)
        newest = max(outstanding, key=lambda c: c.created_at, default=None)
        return newest.used(at) if newest is not None and newest.is_usable(at) else None

    async def delete_spent(self, now: datetime) -> int:
        spent = [c.id for c in self._state.webauthn_challenges.values() if not c.is_usable(now)]
        for challenge_id in spent:
            del self._state.webauthn_challenges[challenge_id]
        return len(spent)


class MemoryProjectRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, project_id: UUID) -> Project | None:
        return self._state.projects.get(project_id)

    async def lock(self, project_id: UUID) -> Project | None:
        # Nothing to lock: in-memory transactions do not interleave.
        return await self.get(project_id)

    async def add(self, project: Project, /) -> None:
        _insert(self._state.projects, project.id, project, "project")

    async def save(self, project: Project, /) -> None:
        stored = self._state.projects.get(project.id)
        if stored is None:
            raise ValueError(f"project {project.id} does not exist")
        if stored.version != project.version:
            raise ConcurrentUpdateError(f"project {project.id} changed concurrently")
        self._state.projects[project.id] = replace(project, version=project.version + 1)

    async def list_for_member(self, user_id: UUID) -> list[Project]:
        member_of = {p for p, u in self._state.memberships if u == user_id}
        live = [
            p for p in self._state.projects.values() if p.id in member_of and not p.is_deleted
        ]
        return sorted(live, key=lambda p: (p.created_at, p.id), reverse=True)


class MemoryMembershipRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, project_id: UUID, user_id: UUID) -> Membership | None:
        return self._state.memberships.get((project_id, user_id))

    async def list_for_project(self, project_id: UUID) -> list[Membership]:
        members = [m for m in self._state.memberships.values() if m.project_id == project_id]
        return sorted(members, key=lambda m: (m.added_at, m.user_id))

    async def add(self, membership: Membership, /) -> None:
        key = (membership.project_id, membership.user_id)
        if key in self._state.memberships:
            raise AlreadyMemberError("This person is already a member of the project.")
        self._state.memberships[key] = membership

    async def change_role(self, project_id: UUID, user_id: UUID, role: Role) -> bool:
        membership = self._state.memberships.get((project_id, user_id))
        if membership is None:
            return False
        self._state.memberships[(project_id, user_id)] = membership.with_role(role)
        return True

    async def remove(self, project_id: UUID, user_id: UUID) -> bool:
        return self._state.memberships.pop((project_id, user_id), None) is not None

    async def remove_all_for_project(self, project_id: UUID) -> int:
        keys = [key for key in self._state.memberships if key[0] == project_id]
        for key in keys:
            del self._state.memberships[key]
        return len(keys)


class MemoryReviewRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, review_id: UUID) -> Review | None:
        return self._state.reviews.get(review_id)

    async def add(self, review: Review, /) -> None:
        _insert(self._state.reviews, review.id, review, "review")

    async def list_for_project(self, project_id: UUID) -> list[Review]:
        reviews = [r for r in self._state.reviews.values() if r.project_id == project_id]
        return sorted(reviews, key=lambda r: (r.created_at, r.id), reverse=True)

    async def save(self, review: Review, /) -> Review:
        stored = self._state.reviews.get(review.id)
        if stored is None:
            raise ValueError(f"review {review.id} does not exist")
        if stored.version != review.version:
            raise ConcurrentUpdateError(f"review {review.id} changed concurrently")
        saved = replace(review, version=review.version + 1)
        self._state.reviews[review.id] = saved
        return saved


class MemoryRunRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, run_id: UUID) -> Run | None:
        return self._state.runs.get(run_id)

    async def lock(self, run_id: UUID) -> Run | None:
        # Nothing to lock: in-memory transactions do not interleave.
        return await self.get(run_id)

    async def add(self, run: Run, /) -> None:
        _insert(self._state.runs, run.id, run, "run")

    async def list_for_review(self, review_id: UUID) -> list[Run]:
        runs = [r for r in self._state.runs.values() if r.review_id == review_id]
        return sorted(runs, key=lambda r: (r.created_at, r.id), reverse=True)

    async def save(self, run: Run, /) -> Run:
        stored = self._state.runs.get(run.id)
        if stored is None:
            raise ValueError(f"run {run.id} does not exist")
        if stored.version != run.version:
            raise ConcurrentUpdateError(f"run {run.id} changed concurrently")
        saved = replace(run, version=run.version + 1)
        self._state.runs[run.id] = saved
        return saved

    async def claim_next(self, worker_id: str, now: datetime, lease: timedelta) -> Run | None:
        queued = [r for r in self._state.runs.values() if r.state is RunState.QUEUED]
        if not queued:
            return None
        oldest = min(queued, key=lambda r: (r.queued_at, r.created_at, r.id))
        return await self.save(oldest.claimed(worker_id, now, lease))

    async def renew_lease(self, run_id: UUID, worker_id: str, until: datetime) -> bool:
        run = self._state.runs.get(run_id)
        if run is None or run.state is not RunState.RUNNING or run.lease_owner != worker_id:
            return False
        await self.save(replace(run, lease_expires_at=until))
        return True

    async def lock_expired(self, now: datetime, limit: int) -> list[Run]:
        expired = [r for r in self._state.runs.values() if r.has_expired_lease(now)]
        return sorted(expired, key=lambda r: (r.lease_expires_at, r.id))[:limit]


class MemoryRunEventRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def next_id(self, run_id: UUID, *, lease_owner: str | None = None) -> int | None:
        run = self._state.runs.get(run_id)
        if run is None or run.is_finished:
            return None
        if lease_owner is not None and (
            run.state is not RunState.RUNNING or run.lease_owner != lease_owner
        ):
            return None
        return len(self._state.run_events.get(run_id, ())) + 1

    async def add(self, event: RunEvent, /) -> None:
        log = self._state.run_events.get(event.run_id, ())
        if event.id != len(log) + 1:
            raise ValueError(f"event {event.id} does not follow event {len(log)}")
        self._state.run_events[event.run_id] = (*log, event)

    async def list_after(self, run_id: UUID, after: int, limit: int) -> list[RunEvent]:
        return list(self._state.run_events.get(run_id, ())[max(after, 0) :][:limit])

    async def last_id(self, run_id: UUID) -> int:
        return len(self._state.run_events.get(run_id, ()))


class MemoryGateRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, gate_id: UUID) -> Gate | None:
        return self._state.gates.get(gate_id)

    async def find(self, run_id: UUID, stage: str, kind: GateKind) -> Gate | None:
        return next(
            (
                g
                for g in self._state.gates.values()
                if (g.run_id, g.stage, g.kind) == (run_id, stage, kind)
            ),
            None,
        )

    async def add(self, gate: Gate, /) -> None:
        if await self.find(gate.run_id, gate.stage, gate.kind) is not None:
            raise ValueError(
                f"run {gate.run_id} already has a {gate.kind} gate at {gate.stage}"
            )
        _insert(self._state.gates, gate.id, gate, "gate")

    async def resolve(self, gate: Gate, /) -> bool:
        stored = self._state.gates.get(gate.id)
        if stored is None or stored.is_resolved:
            return False
        self._state.gates[gate.id] = gate
        return True


class MemoryPaperRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get(self, paper_id: UUID) -> StoredPaper | None:
        return self._state.papers.get(paper_id)

    async def find_by_identity(
        self, review_id: UUID, digests: Collection[bytes]
    ) -> list[StoredPaper]:
        wanted = set(digests)
        return [
            p
            for p in self._state.papers.values()
            if p.review_id == review_id and p.identity_digest in wanted
        ]

    async def add(self, paper: StoredPaper, /) -> None:
        if await self.find_by_identity(paper.review_id, [paper.identity_digest]):
            raise ValueError(f"review {paper.review_id} already has this paper")
        _insert(self._state.papers, paper.id, paper, "paper")

    async def get_set(self, run_id: UUID) -> PaperSet | None:
        return self._state.paper_sets.get(run_id)

    async def add_set(self, paper_set: PaperSet, /) -> bool:
        if paper_set.run_id in self._state.paper_sets:
            return False
        self._state.paper_sets[paper_set.run_id] = paper_set
        return True

    async def add_run_papers(self, papers: Sequence[RunPaper], /) -> None:
        for paper in papers:
            listed = self._state.run_papers.get(paper.run_id, ())
            if any(
                p.paper_id == paper.paper_id or p.position == paper.position for p in listed
            ):
                raise ValueError(f"run {paper.run_id} already lists this paper or position")
            self._state.run_papers[paper.run_id] = (*listed, paper)

    async def list_run_papers(self, run_id: UUID) -> list[RunPaper]:
        return sorted(self._state.run_papers.get(run_id, ()), key=lambda p: p.position)

    async def add_run_sources(self, sources: Sequence[RunSource], /) -> None:
        for source in sources:
            listed = self._state.run_sources.get(source.run_id, ())
            if any(s.source_id == source.source_id for s in listed):
                raise ValueError(f"run {source.run_id} already records {source.source_id}")
            self._state.run_sources[source.run_id] = (*listed, source)

    async def list_run_sources(self, run_id: UUID) -> list[RunSource]:
        return list(self._state.run_sources.get(run_id, ()))

    async def add_text(self, text: PaperText, /) -> bool:
        if text.paper_id in self._state.paper_texts:
            return False
        self._state.paper_texts[text.paper_id] = text
        return True

    async def list_texts(self, paper_ids: Collection[UUID]) -> list[PaperText]:
        return [t for i in paper_ids if (t := self._state.paper_texts.get(i)) is not None]


class MemoryScreeningRepository:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def add(self, decision: StoredDecision, /) -> bool:
        key = (decision.run_id, decision.paper_id)
        if key in self._state.screening_decisions:
            return False
        self._state.screening_decisions[key] = decision
        return True

    async def get(self, run_id: UUID, paper_id: UUID) -> StoredDecision | None:
        return self._state.screening_decisions.get((run_id, paper_id))

    async def list_for_run(self, run_id: UUID) -> list[StoredDecision]:
        return [d for (r, _), d in self._state.screening_decisions.items() if r == run_id]


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
        self.totp_credentials = MemoryTotpCredentialRepository(state)
        self.recovery_codes = MemoryRecoveryCodeRepository(state)
        self.passkeys = MemoryPasskeyRepository(state)
        self.webauthn_challenges = MemoryWebAuthnChallengeRepository(state)
        self.projects = MemoryProjectRepository(state)
        self.memberships = MemoryMembershipRepository(state)
        self.reviews = MemoryReviewRepository(state)
        self.runs = MemoryRunRepository(state)
        self.run_events = MemoryRunEventRepository(state)
        self.gates = MemoryGateRepository(state)
        self.papers = MemoryPaperRepository(state)
        self.screening = MemoryScreeningRepository(state)
        self.plans = MemoryPlanRepository(state.plan_state)
        self.briefs = MemoryDecisionBriefRepository(state.brief_state)
        self.questions = MemoryQuestionRepository(state.question_state)
        self.masking_rules = MemoryMaskingRuleRepository(state.masking_state)
        self.paper_masks = MemoryPaperMaskRepository(state.masking_state)
        self.documents = MemoryDocumentRepository(state.document_state)

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
