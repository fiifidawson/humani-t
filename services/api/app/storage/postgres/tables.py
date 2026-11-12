"""Table definitions, written with SQLAlchemy Core.

Core rather than the ORM: the domain types are frozen dataclasses, and mapping
rows to them explicitly in the repositories keeps SQLAlchemy from reaching into
the domain. Migrations in `migrations/versions/` are the source of truth for
the schema; these definitions must match them.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

metadata = MetaData()

data_keys = Table(
    "data_keys",
    metadata,
    Column("scope_id", Uuid, primary_key=True),
    Column("kek_version", Integer, nullable=False),
    Column("wrapped", LargeBinary, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("destroyed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "(wrapped IS NULL) = (destroyed_at IS NOT NULL)", name="destroyed_iff_unwrapped"
    ),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("sequence", BigInteger, primary_key=True, autoincrement=False),
    Column("id", Uuid, nullable=False, unique=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("actor_id", Uuid, nullable=True),
    Column("target_type", String(64), nullable=False),
    Column("target_id", String(128), nullable=False),
    Column("scope_id", Uuid, nullable=True, index=True),
    Column("request_id", String(64), nullable=True),
    Column("details_sealed", LargeBinary, nullable=True),
    Column("previous_hash", LargeBinary, nullable=False),
    Column("hash", LargeBinary, nullable=False),
)

audit_chain_head = Table(
    "audit_chain_head",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("sequence", BigInteger, nullable=False),
    Column("hash", LargeBinary, nullable=False),
    CheckConstraint("id = 1", name="single_row"),
)

users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("display_name", String(200), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("is_admin", Boolean, nullable=False),
    Column("is_disabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
    CheckConstraint("email = lower(email)", name="email_is_normalised"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("token_hash", LargeBinary, nullable=False, unique=True),
    Column("csrf_token_hash", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("authenticated_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("user_agent", String(256), nullable=True),
    Column("aal", SmallInteger, nullable=False, server_default="1"),
    Column("is_mfa_pending", Boolean, nullable=False, server_default="false"),
    CheckConstraint("aal IN (1, 2)", name="aal_is_known"),
    CheckConstraint("NOT is_mfa_pending OR aal = 1", name="pending_is_aal1"),
)

invitations = Table(
    "invitations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(320), nullable=False),
    Column("token_hash", LargeBinary, nullable=False, unique=True),
    Column("invited_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
)

password_resets = Table(
    "password_resets",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("token_hash", LargeBinary, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)

totp_credentials = Table(
    "totp_credentials",
    metadata,
    Column("id", Uuid, primary_key=True),
    # Unique: one authenticator app per account; the constraint, not a
    # check-then-insert, is what holds under concurrent enrolment.
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, unique=True),
    Column("secret_sealed", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=True),
    Column("last_used_step", BigInteger, nullable=True),
)

recovery_codes = Table(
    "recovery_codes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("code_hash", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)

passkeys = Table(
    "passkeys",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("credential_id", LargeBinary, nullable=False, unique=True),
    Column("public_key", LargeBinary, nullable=False),
    Column("sign_count", BigInteger, nullable=False),
    Column("name", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("transports", String(200), nullable=False, server_default=""),
)

webauthn_challenges = Table(
    "webauthn_challenges",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "session_id",
        Uuid,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("purpose", String(16), nullable=False),
    Column("challenge", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)

projects = Table(
    "projects",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name_sealed", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False, server_default="0"),
)

memberships = Table(
    "memberships",
    metadata,
    Column("project_id", Uuid, ForeignKey("projects.id"), primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True, index=True),
    Column("role", String(16), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("added_by", Uuid, ForeignKey("users.id"), nullable=True),
    CheckConstraint("role IN ('owner', 'reviewer', 'viewer')", name="role_is_known"),
)

reviews = Table(
    "reviews",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False, index=True),
    Column("question_sealed", LargeBinary, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False, index=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("stages", ARRAY(String(16)), nullable=False),
    Column("state", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("queued_at", DateTime(timezone=True), nullable=False),
    Column("stages_finished", SmallInteger, nullable=False),
    Column("stage_started_at", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("cancel_requested_at", DateTime(timezone=True), nullable=True),
    Column("lease_owner", String(128), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("recoveries", SmallInteger, nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
    CheckConstraint(
        "state IN ('queued', 'running', 'waiting', 'done', 'failed', 'cancelled')",
        name="state_is_known",
    ),
    CheckConstraint(
        "(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="lease_is_whole"
    ),
    Index("ix_runs_queue", "queued_at", postgresql_where=text("state = 'queued'")),
    Index("ix_runs_leases", "lease_expires_at", postgresql_where=text("state = 'running'")),
)

run_events = Table(
    "run_events",
    metadata,
    Column("run_id", Uuid, ForeignKey("runs.id"), primary_key=True),
    Column("id", BigInteger, primary_key=True, autoincrement=False),
    Column("name", String(32), nullable=False),
    Column("payload_sealed", LargeBinary, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("id >= 1", name="id_counts_from_one"),
)

gates = Table(
    "gates",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("run_id", Uuid, ForeignKey("runs.id"), nullable=False),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("stage", String(16), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("payload_sealed", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("resolved_by", Uuid, ForeignKey("users.id"), nullable=True),
    Column("decision", String(16), nullable=True),
    Column("revisions_sealed", LargeBinary, nullable=True),
    UniqueConstraint("run_id", "stage", "kind", name="one_gate_per_stage_and_kind"),
    CheckConstraint("kind IN ('vocabulary', 'privacy_review')", name="kind_is_known"),
    CheckConstraint(
        "(resolved_at IS NULL) = (decision IS NULL)", name="resolved_with_a_decision"
    ),
)

papers = Table(
    "papers",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False),
    Column("first_run_id", Uuid, ForeignKey("runs.id"), nullable=False),
    Column("identity_digest", LargeBinary, nullable=False),
    Column("source_id", String(32), nullable=False),
    Column("record_sealed", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("review_id", "identity_digest", name="one_paper_per_identity"),
)

run_paper_sets = Table(
    "run_paper_sets",
    metadata,
    Column("run_id", Uuid, ForeignKey("runs.id"), primary_key=True),
    Column("basis_run_id", Uuid, ForeignKey("runs.id"), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

run_papers = Table(
    "run_papers",
    metadata,
    Column("run_id", Uuid, ForeignKey("runs.id"), primary_key=True),
    Column("paper_id", Uuid, ForeignKey("papers.id"), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("found_by", ARRAY(String(32)), nullable=False),
    UniqueConstraint("run_id", "position", name="one_paper_per_position"),
    CheckConstraint("position >= 1", name="position_counts_from_one"),
)

run_sources = Table(
    "run_sources",
    metadata,
    Column("run_id", Uuid, ForeignKey("runs.id"), primary_key=True),
    Column("source_id", String(32), primary_key=True),
    Column("outcome", String(16), nullable=False),
    Column("found", Integer, nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("vocabulary_version", Integer, nullable=False),
    Column("query_sealed", LargeBinary, nullable=False),
    Column("searched_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("outcome IN ('searched', 'failed')", name="outcome_is_known"),
)

paper_texts = Table(
    "paper_texts",
    metadata,
    Column("paper_id", Uuid, ForeignKey("papers.id"), primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("text_sealed", LargeBinary, nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("kind IN ('abstract', 'title')", name="kind_is_known"),
)

screening_decisions = Table(
    "screening_decisions",
    metadata,
    Column("run_id", Uuid, ForeignKey("runs.id"), primary_key=True),
    Column("paper_id", Uuid, ForeignKey("papers.id"), primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("label", String(16), nullable=False),
    Column("priority", String(16), nullable=False),
    Column("score", Double, nullable=False),
    Column("criteria_matched", ARRAY(String(128)), nullable=False),
    Column("exclusion_reasons", ARRAY(String(128)), nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("prompt_version", String(64), nullable=False),
    Column("criteria_version", Integer, nullable=False),
    Column("evidence_kind", String(16), nullable=False),
    Column("is_unreadable", Boolean, nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("rationale_sealed", LargeBinary, nullable=False),
    # Per-criterion answers and their quotes: none for decisions made without them.
    Column("answers_sealed", LargeBinary, nullable=True),
    CheckConstraint("label IN ('include', 'exclude', 'uncertain')", name="label_is_known"),
    CheckConstraint("priority IN ('high', 'medium', 'low')", name="priority_is_known"),
    CheckConstraint("score >= 0 AND score <= 1", name="score_is_a_fraction"),
    CheckConstraint("criteria_version >= 1", name="criteria_version_counts_from_one"),
)

plan_conversations = Table(
    "plan_conversations",
    metadata,
    Column("review_id", Uuid, ForeignKey("reviews.id"), primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=True),
    Column("body_sealed", LargeBinary, nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
    CheckConstraint("mode IN ('shallow', 'deep')", name="mode_is_known"),
)

plan_versions = Table(
    "plan_versions",
    metadata,
    Column("review_id", Uuid, ForeignKey("reviews.id"), primary_key=True),
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("author_kind", String(16), nullable=False),
    Column("author_id", Uuid, ForeignKey("users.id"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
    Column("accepted_by", Uuid, ForeignKey("users.id"), nullable=True),
    Column("body_sealed", LargeBinary, nullable=False),
    CheckConstraint("version >= 1", name="version_counts_from_one"),
    CheckConstraint("origin IN ('llm_assisted', 'user_supplied')", name="origin_is_known"),
    CheckConstraint("author_kind IN ('model', 'person')", name="author_kind_is_known"),
    CheckConstraint(
        "(author_kind = 'person') = (author_id IS NOT NULL)", name="a_person_is_named"
    ),
    CheckConstraint(
        "origin <> 'user_supplied' OR author_kind = 'person'",
        name="strict_vocabulary_written_by_a_person",
    ),
    CheckConstraint(
        "(accepted_at IS NULL) = (accepted_by IS NULL)", name="accepted_by_someone"
    ),
)

plan_receipts = Table(
    "plan_receipts",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False, index=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("purpose", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=True),
    Column("model_id", String(256), nullable=False),
    Column("prompt_version", String(64), nullable=False),
    Column("is_delivered", Boolean, nullable=False),
    Column("body_sealed", LargeBinary, nullable=False),
    Column("plan_version", Integer, nullable=True),
    Column("change_request_id", Uuid, nullable=True),
    CheckConstraint("purpose IN ('clarify', 'draft', 'revise')", name="purpose_is_known"),
    CheckConstraint(
        "plan_version IS NULL OR plan_version >= 1", name="plan_version_counts_from_one"
    ),
)

masking_rules = Table(
    "masking_rules",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False, index=True),
    Column("term_sealed", LargeBinary, nullable=False),
    Column("entity_type", String(32), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

paper_masks = Table(
    "paper_masks",
    metadata,
    Column("paper_id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("masked_sealed", LargeBinary, nullable=False),
    Column("spans_sealed", LargeBinary, nullable=False),
    Column("masked_with", ARRAY(String(128)), nullable=False),
    Column("masked_at", DateTime(timezone=True), nullable=False),
)

decision_briefs = Table(
    "decision_briefs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False),
    Column("run_id", Uuid, ForeignKey("runs.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("model_id", String(256), nullable=False),
    Column("rounds", SmallInteger, nullable=False),
    Column("kept_round", SmallInteger, nullable=False),
    Column("max_rounds", SmallInteger, nullable=False),
    Column("stopped_because", String(32), nullable=False),
    Column("content_sealed", LargeBinary, nullable=False),
    Column("evidence_sealed", LargeBinary, nullable=False),
    Column("trace_sealed", LargeBinary, nullable=False),
    UniqueConstraint("run_id", name="one_brief_per_run"),
    UniqueConstraint("review_id", "version", name="one_brief_per_version"),
    CheckConstraint(
        "stopped_because IN ('accepted', 'max_rounds', 'invalid_output', 'no_progress')",
        name="stopped_because_is_known",
    ),
    CheckConstraint(
        "1 <= kept_round AND kept_round <= rounds AND rounds <= max_rounds",
        name="rounds_are_consistent",
    ),
)

question_threads = Table(
    "question_threads",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
)

questions = Table(
    "questions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("thread_id", Uuid, ForeignKey("question_threads.id"), nullable=False),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("review_id", Uuid, ForeignKey("reviews.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("asked_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("asked_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("question_sealed", LargeBinary, nullable=False),
    Column("attempts", SmallInteger, nullable=False),
    Column("lease_owner", String(128), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("answered_at", DateTime(timezone=True), nullable=True),
    Column("model_id", String(256), nullable=True),
    Column("answer_sealed", LargeBinary, nullable=True),
    Column("error", String(300), nullable=True),
    Column("version", Integer, nullable=False, server_default="0"),
    UniqueConstraint("thread_id", "position", name="one_question_per_position"),
    CheckConstraint(
        "status IN ('pending', 'answering', 'answered', 'failed')", name="status_is_known"
    ),
    CheckConstraint(
        "(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="lease_is_whole"
    ),
    CheckConstraint(
        "(status = 'answered') = (answer_sealed IS NOT NULL)", name="answered_with_an_answer"
    ),
    Index(
        "ix_questions_queue",
        "asked_at",
        postgresql_where=text("status IN ('pending', 'answering')"),
    ),
)

label_rule_versions = Table(
    "label_rule_versions",
    metadata,
    Column("review_id", Uuid, ForeignKey("reviews.id"), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("possibly_min_met", SmallInteger, nullable=False),
    Column("unclear_counts_as", String(16), nullable=False),
    Column("created_by", Uuid, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("papers_moved", Integer, nullable=False),
    CheckConstraint("version >= 1", name="version_counts_from_one"),
    CheckConstraint("possibly_min_met >= 1", name="possibly_needs_a_criterion"),
    CheckConstraint(
        "unclear_counts_as IN ('met', 'not_met', 'uncertain')",
        name="unclear_counts_as_is_known",
    ),
    CheckConstraint("papers_moved >= 0", name="papers_moved_is_a_count"),
)
