"""Empty a contract-test database between Postgres specs.

The one cleanup every storage contract file shares. The delete order comes from the
schema's own foreign keys, so a new table is cleaned without anyone listing it here.
Fixtures and builders do not belong here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from app.storage.postgres.tables import metadata

# The audit log is append-only by design and the runtime role cannot delete from it,
# so audit tests assume a log that grows correctly, never an empty one.
_KEPT = frozenset({"audit_events", "audit_chain_head"})


async def delete_all_rows(url: str) -> None:
    """Delete every row outside the audit log, children before parents."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            for table in reversed(metadata.sorted_tables):
                if table.name not in _KEPT:
                    await connection.execute(table.delete())
    finally:
        await engine.dispose()
