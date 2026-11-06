"""Create the planning conversation, plan versions and privacy receipts.

Revision ID: 0008
Revises: 0007
Created: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_conversations",
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Null when a run's first stage started the plan, not a person.
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        # Questions, answers and a pending change request, sealed under the project's key.
        sa.Column("body_sealed", sa.LargeBinary, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("mode IN ('shallow', 'deep')", name="mode_is_known"),
    )

    op.create_table(
        "plan_versions",
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("author_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        # Vocabulary, criteria, provenance and the diff, sealed and bound to (review, version).
        sa.Column("body_sealed", sa.LargeBinary, nullable=False),
        sa.CheckConstraint("version >= 1", name="version_counts_from_one"),
        sa.CheckConstraint(
            "origin IN ('llm_assisted', 'user_supplied')", name="origin_is_known"
        ),
        sa.CheckConstraint("author_kind IN ('model', 'person')", name="author_kind_is_known"),
        sa.CheckConstraint(
            "(author_kind = 'person') = (author_id IS NOT NULL)", name="a_person_is_named"
        ),
        # Route B never routes through the model (architecture §4.3), in the schema too.
        sa.CheckConstraint(
            "origin <> 'user_supplied' OR author_kind = 'person'",
            name="strict_vocabulary_written_by_a_person",
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by IS NULL)", name="accepted_by_someone"
        ),
    )

    op.create_table(
        "plan_receipts",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("model_id", sa.String(256), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("is_delivered", sa.Boolean, nullable=False),
        # The masked text the model received, sealed under the project's key.
        sa.Column("body_sealed", sa.LargeBinary, nullable=False),
        sa.CheckConstraint(
            "purpose IN ('clarify', 'draft', 'revise')", name="purpose_is_known"
        ),
    )
    op.create_index("ix_plan_receipts_review_id", "plan_receipts", ["review_id"])

    # An accepted version is frozen, and every version's content is written once:
    # an UPDATE may only record the acceptance of a version not yet accepted.
    op.execute(
        """
        CREATE FUNCTION plan_versions_are_written_once() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.accepted_at IS NOT NULL
               OR (NEW.review_id, NEW.version, NEW.project_id, NEW.origin, NEW.author_kind,
                   NEW.created_at, NEW.body_sealed)
                  IS DISTINCT FROM
                  (OLD.review_id, OLD.version, OLD.project_id, OLD.origin, OLD.author_kind,
                   OLD.created_at, OLD.body_sealed)
               OR NEW.author_id IS DISTINCT FROM OLD.author_id THEN
                RAISE EXCEPTION
                    'a plan version is written once; only its acceptance is recorded';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER plan_versions_written_once
        BEFORE UPDATE ON plan_versions
        FOR EACH ROW EXECUTE FUNCTION plan_versions_are_written_once()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER plan_versions_written_once ON plan_versions")
    op.execute("DROP FUNCTION plan_versions_are_written_once()")
    op.drop_index("ix_plan_receipts_review_id", table_name="plan_receipts")
    op.drop_table("plan_receipts")
    op.drop_table("plan_versions")
    op.drop_table("plan_conversations")
