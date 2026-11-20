"""Add projects.is_view_first_on and leanings: "Your view first" (UI_UX_SPEC §6.9).

The switch is off for every existing project, as for new ones (§16 Q3). A leaning is one
member's answer for one run's brief, its option sealed under the project's key.

Revision ID: 0020
Revises: 0019
Created: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("is_view_first_on", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_table(
        "leanings",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("option_sealed", sa.LargeBinary, nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
    )
    # An answer given "before reading" is worth nothing once rewritten after reading:
    # the app may add one, never change it. Conditional, as in 0009.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON leanings FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("leanings")
    op.drop_column("projects", "is_view_first_on")
