"""Create follow-up questions and their threads; allow the brief's `no_progress` stop.

Revision ID: 0014
Revises: 0013
Created: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STOPS = "('accepted', 'max_rounds', 'invalid_output', 'no_progress')"
_OLD_STOPS = "('accepted', 'max_rounds', 'invalid_output')"


def upgrade() -> None:
    op.drop_constraint("stopped_because_is_known", "decision_briefs", type_="check")
    op.create_check_constraint(
        "stopped_because_is_known", "decision_briefs", f"stopped_because IN {_STOPS}"
    )
    op.create_table(
        "question_threads",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_question_threads_review_id", "question_threads", ["review_id"])
    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("thread_id", sa.Uuid, sa.ForeignKey("question_threads.id"), nullable=False),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("review_id", sa.Uuid, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("asked_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        # Sealed under the project's key and bound to the row: the masked
        # question, and the answer with its evidence and agent steps.
        sa.Column("question_sealed", sa.LargeBinary, nullable=False),
        sa.Column("attempts", sa.SmallInteger, nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_id", sa.String(256), nullable=True),
        sa.Column("answer_sealed", sa.LargeBinary, nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("thread_id", "position", name="one_question_per_position"),
        sa.CheckConstraint(
            "status IN ('pending', 'answering', 'answered', 'failed')", name="status_is_known"
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)", name="lease_is_whole"
        ),
        sa.CheckConstraint(
            "(status = 'answered') = (answer_sealed IS NOT NULL)",
            name="answered_with_an_answer",
        ),
    )
    # Partial: the queue looks only at the few questions still waiting.
    op.create_index(
        "ix_questions_queue",
        "questions",
        ["asked_at"],
        postgresql_where=sa.text("status IN ('pending', 'answering')"),
    )


def downgrade() -> None:
    op.drop_index("ix_questions_queue", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_question_threads_review_id", table_name="question_threads")
    op.drop_table("question_threads")
    op.drop_constraint("stopped_because_is_known", "decision_briefs", type_="check")
    op.create_check_constraint(
        "stopped_because_is_known", "decision_briefs", f"stopped_because IN {_OLD_STOPS}"
    )
