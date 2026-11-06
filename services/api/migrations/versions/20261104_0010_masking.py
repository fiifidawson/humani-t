"""Create project masking rules and papers' maskings.

Revision ID: 0010
Revises: 0009
Created: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "masking_rules",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        # A term is usually a person's name: sealed under the project's key.
        sa.Column("term_sealed", sa.LargeBinary, nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_masking_rules_project_id", "masking_rules", ["project_id"])

    op.create_table(
        "paper_masks",
        sa.Column("paper_id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        # Sealed and bound to the paper's row: a masked text moved to another
        # paper fails to decrypt. The raw text stays in paper_texts.
        sa.Column("masked_sealed", sa.LargeBinary, nullable=False),
        sa.Column("spans_sealed", sa.LargeBinary, nullable=False),
        sa.Column("masked_with", sa.ARRAY(sa.String(128)), nullable=False),
        sa.Column("masked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paper_masks")
    op.drop_index("ix_masking_rules_project_id", table_name="masking_rules")
    op.drop_table("masking_rules")
