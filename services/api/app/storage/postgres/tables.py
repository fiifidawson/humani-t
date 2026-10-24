"""Table definitions, written with SQLAlchemy Core.

Core rather than the ORM: the domain types are frozen dataclasses, and mapping
rows to them explicitly in the repositories keeps SQLAlchemy from reaching into
the domain. Migrations in `migrations/versions/` are the source of truth for
the schema; these definitions must match them.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
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
