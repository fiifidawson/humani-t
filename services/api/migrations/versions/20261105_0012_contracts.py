"""Link privacy receipts to their plan version and change request; seal criterion answers.

Revision ID: 0012
Revises: 0011
Created: 2026-11-05

Every new column is nullable: receipts and decisions written before this
migration have none of them, and the API serves them as null or empty.

The app role's grants are per table, so it can write the new columns already,
and screening decisions stay without UPDATE (migration 0009).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_receipts", sa.Column("plan_version", sa.Integer, nullable=True))
    op.add_column("plan_receipts", sa.Column("change_request_id", sa.Uuid, nullable=True))
    op.create_check_constraint(
        "plan_version_counts_from_one",
        "plan_receipts",
        "plan_version IS NULL OR plan_version >= 1",
    )
    op.add_column(
        "screening_decisions", sa.Column("answers_sealed", sa.LargeBinary, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("screening_decisions", "answers_sealed")
    op.drop_constraint("plan_version_counts_from_one", "plan_receipts", type_="check")
    op.drop_column("plan_receipts", "change_request_id")
    op.drop_column("plan_receipts", "plan_version")
