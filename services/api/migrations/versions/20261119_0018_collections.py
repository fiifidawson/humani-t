"""Create collections, project_collections, project_tags and project_pins (ADR 0016).

Revision ID: 0018
Revises: 0017
Created: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "project_collections",
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column(
            "collection_id",
            sa.Uuid,
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("moved_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("moved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_project_collections_collection_id", "project_collections", ["collection_id"]
    )
    op.create_table(
        "project_tags",
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("tags_sealed", sa.LargeBinary, nullable=False),
        sa.Column("updated_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "project_pins",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_pins_project_id", "project_pins", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_pins")
    op.drop_table("project_tags")
    op.drop_table("project_collections")
    op.drop_table("collections")
