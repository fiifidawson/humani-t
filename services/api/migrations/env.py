"""Alembic environment: runs migrations as the schema-owner role, asynchronously.

The runtime role used by the app cannot create, alter or drop tables. Only this
process, run once per deployment by the `migrate` service, can.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.settings import get_settings
from app.storage.postgres.tables import metadata


def _url() -> str:
    url = get_settings().migration_database_url
    if url is None:
        raise SystemExit("set HUMANI_T_MIGRATION_DATABASE_URL to run migrations")
    return url.get_secret_value()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
