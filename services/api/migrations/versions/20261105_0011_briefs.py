"""Create decision_briefs: one sealed brief per run, versioned within its review.

Revision ID: 0011
Revises: 0010
Created: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_briefs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_id", sa.String(256), nullable=False),
        sa.Column("rounds", sa.SmallInteger, nullable=False),
        sa.Column("kept_round", sa.SmallInteger, nullable=False),
        sa.Column("max_rounds", sa.SmallInteger, nullable=False),
        sa.Column("stopped_because", sa.String(32), nullable=False),
        # Each sealed under the project's data key and bound to this row:
        # the question and body, the evidence the Writer saw, and the trace.
        sa.Column("content_sealed", sa.LargeBinary, nullable=False),
        sa.Column("evidence_sealed", sa.LargeBinary, nullable=False),
        sa.Column("trace_sealed", sa.LargeBinary, nullable=False),
        sa.UniqueConstraint("run_id", name="one_brief_per_run"),
        sa.UniqueConstraint("review_id", "version", name="one_brief_per_version"),
        sa.CheckConstraint(
            "stopped_because IN ('accepted', 'max_rounds', 'invalid_output')",
            name="stopped_because_is_known",
        ),
        sa.CheckConstraint(
            "1 <= kept_round AND kept_round <= rounds AND rounds <= max_rounds",
            name="rounds_are_consistent",
        ),
    )
    # A brief is a record of what was decided: the app may add one, never rewrite it.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON decision_briefs FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("decision_briefs")
