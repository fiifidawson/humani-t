"""Errors about storage and other backing services that every layer may need to recognise.

They live in the domain so the API layer can map them to HTTP without
importing a storage implementation, and so each implementation raises the same
types for the same situations.
"""

from __future__ import annotations


class ConcurrentUpdateError(Exception):
    """The record changed since it was read; the write was refused, not merged.

    Raised instead of silently overwriting a newer version (a lost update).
    The caller re-reads and decides again, or gives up.
    """


class RateLimiterUnavailableError(Exception):
    """The rate limiter's backend cannot be reached.

    Attempts are refused, never let through uncounted: sign-in fails closed,
    and is answered with 503 rather than 500.
    """


class StorageUnavailableError(Exception):
    """No connection could be obtained in time: the database is down or saturated.

    A normal, retryable condition under load, answered with 503 rather than 500.
    """
