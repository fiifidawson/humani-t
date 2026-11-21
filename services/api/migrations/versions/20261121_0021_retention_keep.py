"""Add projects.kept_at and projects.read_at, from which the retention clock also counts.

kept_at is an owner's latest Keep, read_at a member's latest read. Both are empty for every
existing project, so each one's clock runs from its last activity (ADR 0020). With no
retention period set, nothing reads them.

Revision ID: 0021
Revises: 0020
Created: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "read_at")
    op.drop_column("projects", "kept_at")
