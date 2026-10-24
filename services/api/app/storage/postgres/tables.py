"""Table definitions, written with SQLAlchemy Core.

Core rather than the ORM: the domain types are frozen dataclasses, and mapping
rows to them explicitly in the repositories keeps SQLAlchemy from reaching into
the domain. Migrations in `migrations/versions/` are the source of truth for
the schema; these definitions must match them.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    Table,
    Uuid,
)

metadata = MetaData()

data_keys = Table(
    "data_keys",
    metadata,
    Column("scope_id", Uuid, primary_key=True),
    Column("kek_version", Integer, nullable=False),
    Column("wrapped", LargeBinary, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("destroyed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "(wrapped IS NULL) = (destroyed_at IS NOT NULL)", name="destroyed_iff_unwrapped"
    ),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("sequence", BigInteger, primary_key=True, autoincrement=False),
    Column("id", Uuid, nullable=False, unique=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("actor_id", Uuid, nullable=True),
    Column("target_type", String(64), nullable=False),
    Column("target_id", String(128), nullable=False),
    Column("scope_id", Uuid, nullable=True, index=True),
    Column("request_id", String(64), nullable=True),
    Column("details_sealed", LargeBinary, nullable=True),
    Column("previous_hash", LargeBinary, nullable=False),
    Column("hash", LargeBinary, nullable=False),
)

audit_chain_head = Table(
    "audit_chain_head",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("sequence", BigInteger, nullable=False),
    Column("hash", LargeBinary, nullable=False),
    CheckConstraint("id = 1", name="single_row"),
)
