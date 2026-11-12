"""Create label_rule_versions: a review's label rules, one row per applied version.

Revision ID: 0015
Revises: 0014
Created: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "label_rule_versions",
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("possibly_min_met", sa.SmallInteger, nullable=False),
        sa.Column("unclear_counts_as", sa.String(16), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("papers_moved", sa.Integer, nullable=False),
        sa.CheckConstraint("version >= 1", name="version_counts_from_one"),
        sa.CheckConstraint("possibly_min_met >= 1", name="possibly_needs_a_criterion"),
        sa.CheckConstraint(
            "unclear_counts_as IN ('met', 'not_met', 'uncertain')",
            name="unclear_counts_as_is_known",
        ),
        sa.CheckConstraint("papers_moved >= 0", name="papers_moved_is_a_count"),
    )
    # Every label shown is computed from these rows: the app may add a version, never
    # rewrite one. Conditional, as in 0009, for a database set up without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON label_rule_versions FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("label_rule_versions")
