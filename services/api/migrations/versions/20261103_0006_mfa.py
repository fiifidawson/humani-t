"""Add multi-factor authentication: TOTP, recovery codes, passkeys, challenges.

Sessions gain their authenticator assurance level and a pending flag. Existing
sessions become aal 1 and not pending, which under required MFA means their
users must enrol before doing anything else — the safe reading of a session
that never saw a second factor.

Revision ID: 0006
Revises: 0005
Created: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("aal", sa.SmallInteger, nullable=False, server_default="1")
    )
    op.add_column(
        "sessions",
        sa.Column("is_mfa_pending", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint("aal_is_known", "sessions", "aal IN (1, 2)")
    op.create_check_constraint("pending_is_aal1", "sessions", "NOT is_mfa_pending OR aal = 1")

    op.create_table(
        "totp_credentials",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("secret_sealed", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger, nullable=True),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("code_hash", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "passkeys",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("credential_id", sa.LargeBinary, nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary, nullable=False),
        sa.Column("sign_count", sa.BigInteger, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transports", sa.String(200), nullable=False, server_default=""),
    )
    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("challenge", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("webauthn_challenges")
    op.drop_table("passkeys")
    op.drop_table("recovery_codes")
    op.drop_table("totp_credentials")
    op.drop_constraint("pending_is_aal1", "sessions", type_="check")
    op.drop_constraint("aal_is_known", "sessions", type_="check")
    op.drop_column("sessions", "is_mfa_pending")
    op.drop_column("sessions", "aal")
