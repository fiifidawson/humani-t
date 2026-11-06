"""Create data_keys: one wrapped encryption key per scope.

Revision ID: 0001
Revises:
Created: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_keys",
        sa.Column("scope_id", sa.Uuid, primary_key=True),
        sa.Column("kek_version", sa.Integer, nullable=False),
        sa.Column("wrapped", sa.LargeBinary, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        # A key is destroyed exactly when its wrapped bytes are gone. Enforced
        # here as well as in the domain, because a manual UPDATE skips the domain.
        sa.CheckConstraint(
            "(wrapped IS NULL) = (destroyed_at IS NOT NULL)", name="destroyed_iff_unwrapped"
        ),
    )


def downgrade() -> None:
    op.drop_table("data_keys")
