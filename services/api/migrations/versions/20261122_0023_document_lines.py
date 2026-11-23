"""Keep what each line of a Word file's text is, beside the text (ADR 0023).

One nullable column: a list with one kind per line, or NULL for every other file and for
texts read before extractor `local@2`. It holds no words, so it is not sealed, as
`page_starts` is not.

Revision ID: 0023
Revises: 0022
Created: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_texts", sa.Column("lines", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("document_texts", "lines")
