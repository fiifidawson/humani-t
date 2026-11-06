"""Create reviews, runs (also the job queue), the run event log and gates.

Revision ID: 0007
Revises: 0006
Created: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        # Sealed under the project's own data key: unreadable once it is shredded.
        sa.Column("question_sealed", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_reviews_project_id", "reviews", ["project_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("stages", sa.ARRAY(sa.String(16)), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stages_finished", sa.SmallInteger, nullable=False),
        sa.Column("stage_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recoveries", sa.SmallInteger, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'waiting', 'done', 'failed', 'cancelled')",
            name="state_is_known",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="lease_is_whole"
        ),
    )
    op.create_index("ix_runs_review_id", "runs", ["review_id"])
    # Partial indexes: the queue and the lease sweep each look only at the few
    # rows in their state, however many finished runs pile up.
    op.create_index(
        "ix_runs_queue", "runs", ["queued_at"], postgresql_where=sa.text("state = 'queued'")
    )
    op.create_index(
        "ix_runs_leases",
        "runs",
        ["lease_expires_at"],
        postgresql_where=sa.text("state = 'running'"),
    )

    op.create_table(
        "run_events",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(32), nullable=False),
        # Sealed under the project's key and bound to (run, id): a payload moved
        # to another event fails to decrypt, so the log cannot be reordered.
        sa.Column("payload_sealed", sa.LargeBinary, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id >= 1", name="id_counts_from_one"),
    )

    op.create_table(
        "gates",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("revisions_sealed", sa.LargeBinary, nullable=True),
        sa.UniqueConstraint("run_id", "stage", "kind", name="one_gate_per_stage_and_kind"),
        sa.CheckConstraint("kind IN ('vocabulary', 'privacy_review')", name="kind_is_known"),
        sa.CheckConstraint(
            "(resolved_at IS NULL) = (decision IS NULL)", name="resolved_with_a_decision"
        ),
    )

    # An event, once stored, is history: the app may add and (for retention)
    # delete, never rewrite. Conditional, as in 0002, for a database set up
    # without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON run_events FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("gates")
    op.drop_table("run_events")
    op.drop_index("ix_runs_leases", table_name="runs")
    op.drop_index("ix_runs_queue", table_name="runs")
    op.drop_index("ix_runs_review_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_reviews_project_id", table_name="reviews")
    op.drop_table("reviews")
