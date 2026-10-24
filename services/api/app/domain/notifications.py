"""Messages the system sends to people. The transport lives in `app/email`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailDeliveryError(Exception):
    """The mail server refused or could not be reached."""
