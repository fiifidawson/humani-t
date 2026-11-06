"""Create papers, what each run discovered, kept texts and screening decisions.

Revision ID: 0009
Revises: 0008
Created: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("first_run_id", sa.Uuid, sa.ForeignKey("runs.id"), nullable=False),
        # HMAC of the paper's identity (DOI, or title and year) under a subkey of
        # the project's data key: re-runs find the paper, a database copy learns nothing.
        sa.Column("identity_digest", sa.LargeBinary, nullable=False),
        sa.Column("source_id", sa.String(32), nullable=False),
        # The whole PaperRecord, sealed under the project's key and bound to the row.
        sa.Column("record_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("review_id", "identity_digest", name="one_paper_per_identity"),
    )

    op.create_table(
        "run_paper_sets",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("basis_run_id", sa.Uuid, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "run_papers",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("found_by", sa.ARRAY(sa.String(32)), nullable=False),
        sa.UniqueConstraint("run_id", "position", name="one_paper_per_position"),
        sa.CheckConstraint("position >= 1", name="position_counts_from_one"),
    )

    op.create_table(
        "run_sources",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("source_id", sa.String(32), primary_key=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("found", sa.Integer, nullable=False),
        sa.Column("plan_version", sa.Integer, nullable=False),
        sa.Column("vocabulary_version", sa.Integer, nullable=False),
        sa.Column("query_sealed", sa.LargeBinary, nullable=False),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('searched', 'failed')", name="outcome_is_known"),
    )

    op.create_table(
        "paper_texts",
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        # Raw text, sealed: what the mask stage reads. No model ever sees it.
        sa.Column("text_sealed", sa.LargeBinary, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('abstract', 'title')", name="kind_is_known"),
    )

    op.create_table(
        "screening_decisions",
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("label", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("score", sa.Double, nullable=False),
        sa.Column("criteria_matched", sa.ARRAY(sa.String(128)), nullable=False),
        sa.Column("exclusion_reasons", sa.ARRAY(sa.String(128)), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("criteria_version", sa.Integer, nullable=False),
        sa.Column("evidence_kind", sa.String(16), nullable=False),
        sa.Column("is_unreadable", sa.Boolean, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale_sealed", sa.LargeBinary, nullable=False),
        sa.CheckConstraint(
            "label IN ('include', 'exclude', 'uncertain')", name="label_is_known"
        ),
        sa.CheckConstraint("priority IN ('high', 'medium', 'low')", name="priority_is_known"),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="score_is_a_fraction"),
        sa.CheckConstraint("criteria_version >= 1", name="criteria_version_counts_from_one"),
    )

    # A decision is provenance: the app may add one, never rewrite it. Conditional,
    # as in 0002 and 0007, for a database set up without our role script.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, TRUNCATE ON screening_decisions FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("screening_decisions")
    op.drop_table("paper_texts")
    op.drop_table("run_sources")
    op.drop_table("run_papers")
    op.drop_table("run_paper_sets")
    op.drop_table("papers")
