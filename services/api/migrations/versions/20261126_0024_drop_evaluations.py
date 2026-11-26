"""Drop evaluations: evaluation admin is removed (ADR 0022, withdrawn).

Whatever an evaluation held was sealed under its project's key, and goes with the row.
The downgrade brings back the empty table, not what was in it.

Revision ID: 0024
Revises: 0023
Created: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_evaluations_queue", table_name="evaluations")
    op.drop_index("ix_evaluations_project_id", table_name="evaluations")
    op.drop_table("evaluations")


def downgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("judge_model", sa.String(256), nullable=False),
        sa.Column("question_count", sa.SmallInteger, nullable=False),
        sa.Column("system_count", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("setup_sealed", sa.LargeBinary, nullable=False),
        sa.Column("results_sealed", sa.LargeBinary, nullable=True),
        sa.Column("answered_count", sa.SmallInteger, nullable=False),
        sa.Column("writer_model", sa.String(256), nullable=True),
        sa.Column("attempts", sa.SmallInteger, nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed')", name="status_is_known"
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="lease_is_whole"
        ),
        sa.CheckConstraint(
            "answered_count BETWEEN 0 AND question_count", name="answered_within_questions"
        ),
    )
    op.create_index("ix_evaluations_project_id", "evaluations", ["project_id"])
    op.create_index(
        "ix_evaluations_queue",
        "evaluations",
        ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
