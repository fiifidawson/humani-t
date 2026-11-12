"""Persistence as repositories behind a unit of work.

No SQL and no SQLAlchemy types may appear above this package. Use cases talk to
these Protocols, are tested against `memory.py`, and run against `postgres/`.
A shared contract suite (`tests/test_storage_contract.py`) runs against both, so
the in-memory version cannot drift from the real one.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.accounts import Invitation, PasswordReset, User
from app.domain.audit import AuditEvent, ChainHead
from app.domain.briefs import BriefRecord
from app.domain.discovery import PaperSet, PaperText, RunPaper, RunSource, StoredPaper
from app.domain.documents import Document, DocumentMask, DocumentReview, DocumentText
from app.domain.encryption import WrappedDataKey
from app.domain.masking import MaskingRuleRecord, PaperMask
from app.domain.mfa import (
    ChallengePurpose,
    Passkey,
    RecoveryCode,
    TotpCredential,
    WebAuthnChallenge,
)
from app.domain.plans import StoredConversation, StoredPlanVersion, StoredReceipt
from app.domain.projects import Membership, Project, Role
from app.domain.questions import QuestionRecord, Thread
from app.domain.reviews import Review
from app.domain.runs import Gate, GateKind, Run, RunEvent
from app.domain.screening import StoredDecision
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


# Writes that can race are never blind overwrites: they are conditional and report
# whether they won, or version-checked (`UserRepository.save`). Records are
# positional-only (`/`) so implementations may name them freely.
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

    async def lock_enabled_admins(self) -> list[User]:
        """Lock and return every enabled administrator, until the transaction ends.

        Without this lock, two administrators disabling each other could leave none.
        """
        ...


class SessionRepository(Protocol):
    async def get(self, session_id: UUID) -> Session | None: ...

    async def get_by_token_hash(self, token_hash: bytes) -> Session | None: ...

    async def add(self, session: Session, /) -> None: ...

    async def touch(self, session_id: UUID, at: datetime) -> None:
        """Record activity. Never affects a revoked session."""
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


class TotpCredentialRepository(Protocol):
    async def get_for_user(self, user_id: UUID) -> TotpCredential | None:
        """The user's TOTP credential, confirmed or not; a user has at most one."""
        ...

    async def add(self, credential: TotpCredential, /) -> None:
        """Raise `TotpAlreadyEnrolledError` if the user already has one."""
        ...

    async def confirm(self, credential_id: UUID, at: datetime) -> bool:
        """Mark confirmed if still unconfirmed; True only for the one caller that won."""
        ...

    async def advance_step(self, credential_id: UUID, step: int) -> bool:
        """Record `step` as used if it is later than the last; the replay guard."""
        ...

    async def delete(self, credential_id: UUID) -> bool: ...


class RecoveryCodeRepository(Protocol):
    async def replace_for_user(self, user_id: UUID, codes: Sequence[RecoveryCode], /) -> None:
        """Remove every code of the user, used or not, and store `codes` instead."""
        ...

    async def claim(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        """Mark the user's unused code with this hash as used; True only for the winner."""
        ...

    async def count_unused(self, user_id: UUID) -> int: ...


class PasskeyRepository(Protocol):
    async def get(self, passkey_id: UUID) -> Passkey | None: ...

    async def get_by_credential_id(self, credential_id: bytes) -> Passkey | None: ...

    async def list_for_user(self, user_id: UUID) -> list[Passkey]:
        """Oldest first."""
        ...

    async def add(self, passkey: Passkey, /) -> None:
        """Raise `PasskeyAlreadyRegisteredError` if the credential id is taken, by anyone."""
        ...

    async def record_use(self, passkey_id: UUID, sign_count: int, at: datetime) -> bool:
        """Store a sign-in if the counter advanced (or both are zero); True for the winner."""
        ...

    async def delete(self, passkey_id: UUID) -> bool: ...


class WebAuthnChallengeRepository(Protocol):
    async def add(self, challenge: WebAuthnChallenge, /) -> None: ...

    async def take(
        self, session_id: UUID, purpose: ChallengePurpose, at: datetime
    ) -> WebAuthnChallenge | None:
        """Spend every outstanding challenge of the session for this purpose.

        Return the newest one if it was still usable, else `None`. Spending is
        unconditional: a challenge answers at most one attempt, failed or not.
        """
        ...

    async def delete_spent(self, now: datetime) -> int:
        """Delete used and expired challenges; return how many."""
        ...


class ProjectRepository(Protocol):
    async def get(self, project_id: UUID) -> Project | None:
        """Deleted projects are returned too, as tombstones; callers check `is_deleted`."""
        ...

    async def lock(self, project_id: UUID) -> Project | None:
        """Read the project and hold its row until the transaction ends.

        Membership changes serialise through this lock; without it two owners
        stepping down at once could each see the other still an owner.
        """
        ...

    async def add(self, project: Project, /) -> None: ...

    async def save(self, project: Project, /) -> None:
        """Store `project` if its `version` is current, else raise `ConcurrentUpdateError`."""
        ...

    async def list_for_member(self, user_id: UUID) -> list[Project]:
        """Live projects the user belongs to, newest first."""
        ...


class MembershipRepository(Protocol):
    async def get(self, project_id: UUID, user_id: UUID) -> Membership | None: ...

    async def list_for_project(self, project_id: UUID) -> list[Membership]:
        """Every member, earliest added first."""
        ...

    async def add(self, membership: Membership, /) -> None:
        """Raise `AlreadyMemberError` if the user already belongs to the project."""
        ...

    async def change_role(self, project_id: UUID, user_id: UUID, role: Role) -> bool:
        """False if the user is no longer a member."""
        ...

    async def remove(self, project_id: UUID, user_id: UUID) -> bool:
        """False if the user was not a member."""
        ...

    async def remove_all_for_project(self, project_id: UUID) -> int: ...


class ReviewRepository(Protocol):
    async def get(self, review_id: UUID) -> Review | None: ...

    async def add(self, review: Review, /) -> None: ...

    async def list_for_project(self, project_id: UUID) -> list[Review]:
        """The project's reviews, newest first."""
        ...

    async def save(self, review: Review, /) -> Review:
        """Store `review` if its `version` is current and return the stored copy.

        Raise `ConcurrentUpdateError` if it is not.
        """
        ...


class RunRepository(Protocol):
    """Runs, and the job queue they form: a queued run is a job waiting for a worker."""

    async def get(self, run_id: UUID) -> Run | None: ...

    async def lock(self, run_id: UUID) -> Run | None:
        """Read the run and hold its row until the transaction ends.

        Every change to a run is made under this lock, so a cancel, a gate
        decision and the worker are decided one after the other.
        """
        ...

    async def add(self, run: Run, /) -> None: ...

    async def list_for_review(self, review_id: UUID) -> list[Run]:
        """The review's runs, newest first."""
        ...

    async def save(self, run: Run, /) -> Run:
        """Store `run` if its `version` is current and return the stored copy.

        Raise `ConcurrentUpdateError` if it is not.
        """
        ...

    async def claim_next(self, worker_id: str, now: datetime, lease: timedelta) -> Run | None:
        """Hand the longest-queued run to `worker_id` under a lease; `None` if none waits.

        A run another transaction is claiming is skipped, not waited for, so
        workers never queue behind each other.
        """
        ...

    async def renew_lease(self, run_id: UUID, worker_id: str, until: datetime) -> bool:
        """Extend the lease. False if the run is not running under `worker_id` any more."""
        ...

    async def lock_expired(self, now: datetime, limit: int) -> list[Run]:
        """Lock up to `limit` running runs whose lease ended before `now`.

        Runs another transaction holds are skipped, not waited for.
        """
        ...


class RunEventRepository(Protocol):
    async def next_id(self, run_id: UUID, *, lease_owner: str | None = None) -> int | None:
        """Lock the run and return the id its next event must have.

        `None` if the run is missing or finished, or, with `lease_owner`, if that worker
        no longer holds the lease.
        """
        ...

    async def add(self, event: RunEvent, /) -> None:
        """Append an event. Requires `next_id` in the same transaction."""
        ...

    async def list_after(self, run_id: UUID, after: int, limit: int) -> list[RunEvent]:
        """Events with an id greater than `after`, in id order."""
        ...

    async def last_id(self, run_id: UUID) -> int:
        """The id of the run's latest event, 0 if it has none."""
        ...


class GateRepository(Protocol):
    async def get(self, gate_id: UUID) -> Gate | None: ...

    async def find(self, run_id: UUID, stage: str, kind: GateKind) -> Gate | None: ...

    async def add(self, gate: Gate, /) -> None: ...

    async def resolve(self, gate: Gate, /) -> bool:
        """Store `gate`'s resolution if it is still open; True only for the one that won."""
        ...


class PaperRepository(Protocol):
    """A review's papers, which runs saw them, which sources were asked, and kept texts.

    A run's papers are filed under the run that discovered them; a run that
    adopted another's discovery reads them through its `PaperSet`.
    """

    async def get(self, paper_id: UUID) -> StoredPaper | None: ...

    async def find_by_identity(
        self, review_id: UUID, digests: Collection[bytes]
    ) -> list[StoredPaper]:
        """The review's papers with one of these identity digests, in no particular order."""
        ...

    async def add(self, paper: StoredPaper, /) -> None: ...

    async def get_set(self, run_id: UUID) -> PaperSet | None: ...

    async def add_set(self, paper_set: PaperSet, /) -> bool:
        """Record the run's paper set if it has none; True only for the one that won."""
        ...

    async def add_run_papers(self, papers: Sequence[RunPaper], /) -> None: ...

    async def list_run_papers(self, run_id: UUID) -> list[RunPaper]:
        """The papers a run discovered, by position."""
        ...

    async def add_run_sources(self, sources: Sequence[RunSource], /) -> None: ...

    async def list_run_sources(self, run_id: UUID) -> list[RunSource]: ...

    async def add_text(self, text: PaperText, /) -> bool:
        """Keep the paper's text if it has none yet; True only for the one that won."""
        ...

    async def list_texts(self, paper_ids: Collection[UUID]) -> list[PaperText]: ...


class ScreeningRepository(Protocol):
    async def add(self, decision: StoredDecision, /) -> bool:
        """Store the run's decision on the paper unless it has one; True only for the winner.

        A decision is never replaced: a re-run of the stage skips the paper.
        """
        ...

    async def get(self, run_id: UUID, paper_id: UUID) -> StoredDecision | None: ...

    async def list_for_run(self, run_id: UUID) -> list[StoredDecision]: ...


class PlanRepository(Protocol):
    """A review's planning conversation, its plan versions and its privacy receipts."""

    async def get_conversation(self, review_id: UUID) -> StoredConversation | None: ...

    async def add_conversation(self, conversation: StoredConversation, /) -> None:
        """Raise `ConcurrentUpdateError` if the review already has one."""
        ...

    async def save_conversation(
        self, conversation: StoredConversation, /
    ) -> StoredConversation:
        """Store it if its `version` is current and return the stored copy.

        Raise `ConcurrentUpdateError` if it is not.
        """
        ...

    async def add_version(self, version: StoredPlanVersion, /) -> None:
        """Raise `ConcurrentUpdateError` if the review already has this version number.

        Two writers drafting "the next version" at once: one stores it, the other
        learns it lost.
        """
        ...

    async def get_version(self, review_id: UUID, version: int) -> StoredPlanVersion | None: ...

    async def latest_version(self, review_id: UUID) -> StoredPlanVersion | None: ...

    async def latest_accepted(self, review_id: UUID) -> StoredPlanVersion | None: ...

    async def list_versions(self, review_id: UUID) -> list[StoredPlanVersion]:
        """Oldest first."""
        ...

    async def accept(self, review_id: UUID, version: int, by: UUID, at: datetime) -> bool:
        """Mark the version accepted if it is the latest and not yet accepted.

        True only for the one caller that won; an accepted version is never rewritten.
        """
        ...

    async def add_receipt(self, receipt: StoredReceipt, /) -> None: ...

    async def list_receipts(self, review_id: UUID) -> list[StoredReceipt]:
        """Oldest first."""
        ...


class QuestionRepository(Protocol):
    """Follow-up questions, their threads, and the queue of questions waiting for an answer."""

    async def add_thread(self, thread: Thread, /) -> None: ...

    async def get_thread(self, thread_id: UUID) -> Thread | None: ...

    async def list_threads(self, review_id: UUID) -> list[Thread]:
        """The review's threads, newest first."""
        ...

    async def add(self, question: QuestionRecord, /) -> None:
        """Raise `ConcurrentUpdateError` if its thread already has a question there."""
        ...

    async def get(self, question_id: UUID) -> QuestionRecord | None: ...

    async def list_for_thread(self, thread_id: UUID) -> list[QuestionRecord]:
        """By position."""
        ...

    async def save(self, question: QuestionRecord, /) -> QuestionRecord:
        """Store it if its `version` is current and return the stored copy.

        Raise `ConcurrentUpdateError` if it is not.
        """
        ...

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> QuestionRecord | None:
        """Hand the longest-waiting claimable question to `worker_id` under a lease.

        Claimable: pending, or answering under a lease that lapsed with attempts
        left. A question another transaction is claiming is skipped, not waited for.
        """
        ...

    async def renew_lease(self, question_id: UUID, worker_id: str, until: datetime) -> bool:
        """Extend the lease; False if the question is no longer `worker_id`'s to answer."""
        ...

    async def lock_exhausted(self, now: datetime, limit: int) -> list[QuestionRecord]:
        """Lock up to `limit` questions whose lease lapsed with no attempt left."""
        ...


class DecisionBriefRepository(Protocol):
    """Decision briefs: one per run, numbered by version within their review."""

    async def add(self, brief: BriefRecord, /) -> bool:
        """Store the brief unless its run already has one; True only for the one that won.

        A stage run twice, or two workers racing on one run, never stores two.
        """
        ...

    async def get(self, brief_id: UUID) -> BriefRecord | None: ...

    async def for_run(self, run_id: UUID) -> BriefRecord | None: ...

    async def latest_for_review(self, review_id: UUID) -> BriefRecord | None:
        """The review's brief with the highest version, or `None` before the first."""
        ...

    async def count_for_review(self, review_id: UUID) -> int: ...


class MaskingRuleRepository(Protocol):
    """A project's "always mask this term" rules; each term is stored sealed."""

    async def get(self, rule_id: UUID) -> MaskingRuleRecord | None: ...

    async def add(self, rule: MaskingRuleRecord, /) -> None: ...

    async def list_for_project(self, project_id: UUID) -> list[MaskingRuleRecord]:
        """Oldest first."""
        ...

    async def delete(self, rule_id: UUID) -> bool:
        """False if there was no such rule."""
        ...


class PaperMaskRepository(Protocol):
    """Each paper's current masking: sealed masked text and spans, and what made them."""

    async def get(self, paper_id: UUID) -> PaperMask | None: ...

    async def put(self, mask: PaperMask, /, *, replaces: tuple[str, ...] | None) -> bool:
        """Store `mask` if the paper's stored masking is still `replaces` (`None`: none yet).

        False if another writer got there first; the caller re-reads, never overwrites.
        """
        ...


class DocumentRepository(Protocol):
    """A project's uploaded documents, their raw text, their current masking and reviews.

    A document row is also its processing job: an `uploaded` document waits for
    a worker, which claims it under a lease like a run.
    """

    async def get(self, document_id: UUID) -> Document | None: ...

    async def list_for_project(self, project_id: UUID) -> list[Document]:
        """By number, oldest first."""
        ...

    async def next_number(self, project_id: UUID) -> int:
        """One more than the highest number the project ever used. Under the project's lock."""
        ...

    async def add(self, document: Document, /) -> None: ...

    async def save(self, document: Document, /) -> Document:
        """Store `document` if its `version` is current and return the stored copy.

        Raise `ConcurrentUpdateError` if it is not, or if the document is gone.
        """
        ...

    async def claim_next(
        self, worker_id: str, now: datetime, lease: timedelta
    ) -> Document | None:
        """Hand the longest-waiting uploaded document to `worker_id`; `None` if none waits.

        A document another transaction is claiming is skipped, not waited for.
        """
        ...

    async def renew_lease(self, document_id: UUID, worker_id: str, until: datetime) -> bool:
        """Extend the lease. False if `worker_id` is no longer processing the document."""
        ...

    async def lock_expired(self, now: datetime, limit: int) -> list[Document]:
        """Lock up to `limit` documents being processed whose lease ended before `now`."""
        ...

    async def delete(self, document_id: UUID) -> bool:
        """Remove the document with its raw text and masking. False if it was not there."""
        ...

    async def add_text(self, text: DocumentText, /) -> bool:
        """Keep the extracted text if the document has none yet; True only for the winner."""
        ...

    async def get_text(self, document_id: UUID) -> DocumentText | None: ...

    async def put_mask(self, mask: DocumentMask, /, *, replaces: int | None) -> bool:
        """Store `mask` if the current masking is still version `replaces` (`None`: none yet).

        False if another writer got there first; never an overwrite.
        """
        ...

    async def get_mask(self, document_id: UUID) -> DocumentMask | None: ...

    async def add_review(self, review: DocumentReview, /) -> None:
        """Record a decision. Reviews are never changed, and outlive their document."""
        ...

    async def list_reviews(self, document_id: UUID) -> list[DocumentReview]:
        """Oldest first."""
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

    @property
    def totp_credentials(self) -> TotpCredentialRepository: ...

    @property
    def recovery_codes(self) -> RecoveryCodeRepository: ...

    @property
    def passkeys(self) -> PasskeyRepository: ...

    @property
    def webauthn_challenges(self) -> WebAuthnChallengeRepository: ...

    @property
    def projects(self) -> ProjectRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def reviews(self) -> ReviewRepository: ...

    @property
    def runs(self) -> RunRepository: ...

    @property
    def run_events(self) -> RunEventRepository: ...

    @property
    def gates(self) -> GateRepository: ...

    @property
    def papers(self) -> PaperRepository: ...

    @property
    def screening(self) -> ScreeningRepository: ...

    @property
    def plans(self) -> PlanRepository: ...

    @property
    def briefs(self) -> DecisionBriefRepository: ...

    @property
    def questions(self) -> QuestionRepository: ...

    @property
    def masking_rules(self) -> MaskingRuleRepository: ...

    @property
    def paper_masks(self) -> PaperMaskRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

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
