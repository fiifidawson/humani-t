"""Structured logging with secrets and personal data redacted before output.

Redaction is by key name and happens in the logging pipeline itself, so a
careless `log.info("login", password=...)` is caught here rather than relying
on every call site to remember.
"""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"pass(word)?|secret|token|cookie|authorization|api[_-]?key|email|session|otp|recovery",
    re.IGNORECASE,
)


def redact_sensitive(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event:
        if key != "event" and _SENSITIVE_KEY.search(key):
            event[key] = REDACTED
    return event


def configure_logging(level: str, *, as_json: bool) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer() if as_json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
