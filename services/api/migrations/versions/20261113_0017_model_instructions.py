"""Create model_instruction_versions: a project's instructions to the model, per save.

Revision ID: 0017
Revises: 0016
Created: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_instruction_versions",
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("instructions_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="version_counts_from_one"),
    )
    # A run's provenance names a version of these rows: the app may add a version, never
    # rewrite one. Conditional, as in 0009, for a database set up without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON model_instruction_versions FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("model_instruction_versions")
