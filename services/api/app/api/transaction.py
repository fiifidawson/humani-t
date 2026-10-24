"""One transaction per route, committed when the route's work is done."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.storage.base import StorageProvider, UnitOfWork


@asynccontextmanager
async def transaction(
    storage: StorageProvider, *, commit_on: tuple[type[Exception], ...] = ()
) -> AsyncIterator[UnitOfWork]:
    """Commit on success, and also when one of `commit_on` is raised.

    A failed sign-in is the case in point: the request fails, but its audit
    record must still be kept, or the audit log would miss every attack.
    """
    async with storage.unit_of_work() as uow:
        try:
            yield uow
        except commit_on:
            await uow.commit()
            raise
        await uow.commit()
