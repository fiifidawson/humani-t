"""Create projects and memberships.

Revision ID: 0005
Revises: 0004
Created: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid, primary_key=True),
        # Sealed under the project's own data key: unreadable once it is shredded.
        sa.Column("name_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "memberships",
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("added_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'reviewer', 'viewer')", name="role_is_known"),
    )
    # The primary key serves lookups by project; this serves "my projects".
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("projects")
