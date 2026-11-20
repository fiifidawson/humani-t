"""Create paper_marks, brief_reads and needs_you_dismissals: a person's own marks.

Revision ID: 0019
Revises: 0018
Created: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_marks",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("identity_sealed", sa.LargeBinary, nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('seen', 'saved')", name="kind_is_known"),
    )
    op.create_table(
        "brief_reads",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("brief_id", sa.Uuid, sa.ForeignKey("decision_briefs.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "needs_you_dismissals",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("item_key", sa.String(120), primary_key=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Marks are added and a saved mark removed, never rewritten. Conditional, as in 0009,
    # for a database set up without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON paper_marks, brief_reads, needs_you_dismissals
                    FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("needs_you_dismissals")
    op.drop_table("brief_reads")
    op.drop_table("paper_marks")
