"""Create uploaded documents, their raw text, maskings, reviews and tag numbers.

Revision ID: 0013
Revises: 0012
Created: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        # A file name can name a person ("interview_amina.docx"): sealed.
        sa.Column("name_sealed", sa.LargeBinary, nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure", sa.String(32), nullable=True),
        sa.Column("scanned_with", sa.String(128), nullable=True),
        sa.Column("extracted_with", sa.String(128), nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("character_count", sa.Integer, nullable=True),
        sa.Column("is_truncated", sa.Boolean, nullable=False),
        # Entity types and counts only, never values (brief rule 4).
        sa.Column("masked_counts", JSONB, nullable=False),
        sa.Column("mask_version", sa.Integer, nullable=False),
        sa.Column("uploaded_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.SmallInteger, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.UniqueConstraint("project_id", "number", name="uq_documents_project_number"),
        sa.CheckConstraint(
            "status IN ('uploaded', 'scanning', 'extracting', 'masking', 'awaiting_review', "
            "'approved', 'rejected', 'failed')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("number >= 1", name="ck_documents_number_counts_from_one"),
        sa.CheckConstraint("size_bytes >= 1", name="ck_documents_size_is_positive"),
    )
    op.create_index(
        "ix_documents_queue",
        "documents",
        ["uploaded_at"],
        postgresql_where=sa.text("status = 'uploaded'"),
    )
    op.create_index(
        "ix_documents_leases",
        "documents",
        ["lease_until"],
        postgresql_where=sa.text("status IN ('scanning', 'extracting', 'masking')"),
    )

    # The highest tag number a project ever used, so a deleted document's tag
    # (D3) is never given to another document a brief might be read beside.
    op.create_table(
        "document_numbers",
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("highest_number", sa.Integer, nullable=False),
    )

    op.create_table(
        "document_texts",
        sa.Column(
            "document_id",
            sa.Uuid,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        # The raw extracted text: sealed, bound to the document's row, and read
        # only by masking and the owner's review screen (architecture §3.9.1).
        sa.Column("raw_sealed", sa.LargeBinary, nullable=False),
        sa.Column("page_starts", sa.ARRAY(sa.Integer), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "document_masks",
        sa.Column(
            "document_id",
            sa.Uuid,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        # Bound to the document and the version: an older masking copied over
        # a newer one fails to decrypt.
        sa.Column("masked_sealed", sa.LargeBinary, nullable=False),
        sa.Column("detail_sealed", sa.LargeBinary, nullable=False),
        sa.Column("masked_with", sa.ARRAY(sa.String(128)), nullable=False),
        sa.Column("masked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("masked_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_document_masks_version_counts_from_one"),
    )

    op.create_table(
        "document_reviews",
        sa.Column("id", sa.Uuid, primary_key=True),
        # No foreign key: who approved which version is provenance, kept after
        # the document itself is deleted. It holds ids only.
        sa.Column("document_id", sa.Uuid, nullable=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("mask_version", sa.Integer, nullable=False),
        sa.Column("decided_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')", name="ck_document_reviews_decision"
        ),
    )
    op.create_index("ix_document_reviews_document_id", "document_reviews", ["document_id"])

    # A review is provenance: the app may add one, never rewrite it. Conditional,
    # as in 0002 and 0007, for a database set up without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON document_reviews FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index("ix_document_reviews_document_id", table_name="document_reviews")
    op.drop_table("document_reviews")
    op.drop_table("document_masks")
    op.drop_table("document_texts")
    op.drop_table("document_numbers")
    op.drop_index("ix_documents_leases", table_name="documents")
    op.drop_index("ix_documents_queue", table_name="documents")
    op.drop_table("documents")
