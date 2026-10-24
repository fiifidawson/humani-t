"""Create the append-only, hash-chained audit log.

Revision ID: 0002
Revises: 0001
Created: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("sequence", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("id", sa.Uuid, nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid, nullable=True),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("scope_id", sa.Uuid, nullable=True, index=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details_sealed", sa.LargeBinary, nullable=True),
        sa.Column("previous_hash", sa.LargeBinary, nullable=False),
        sa.Column("hash", sa.LargeBinary, nullable=False),
    )
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.SmallInteger, primary_key=True),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("hash", sa.LargeBinary, nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
    )
    # The genesis head: no events yet, and an all-zero "previous hash".
    op.execute(
        "INSERT INTO audit_chain_head (id, sequence, hash) "
        "VALUES (1, 0, decode(repeat('00', 32), 'hex'))"
    )

    # Two layers. The trigger stops every role, including the schema owner, from
    # editing history by accident. The REVOKE means a compromised app process
    # cannot even ask. A superuser can still disable the trigger; the hash chain
    # is what makes that detectable afterwards.
    op.execute(
        """
        CREATE FUNCTION audit_events_are_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_are_append_only()
        """
    )
    # Conditional so the migration also runs against a database set up without
    # our role script (a developer's own Postgres); the trigger still applies.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humani_t_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM humani_t_app;
                REVOKE DELETE, TRUNCATE, INSERT ON audit_chain_head FROM humani_t_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION audit_events_are_append_only()")
    op.drop_table("audit_chain_head")
    op.drop_table("audit_events")
