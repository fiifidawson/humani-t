"""Add projects.is_masking_on and plan_receipts.is_masked: masking as a project's choice.

Every existing project was masked until now, so both columns are backfilled true: the
switch keeps them masked, and their receipts say truthfully that masking was on (ADR 0014).

Revision ID: 0016
Revises: 0015
Created: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("is_masking_on", sa.Boolean, nullable=False, server_default="true"),
    )
    op.add_column(
        "plan_receipts",
        sa.Column("is_masked", sa.Boolean, nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("plan_receipts", "is_masked")
    op.drop_column("projects", "is_masking_on")
