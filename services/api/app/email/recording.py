"""Keeps sent messages in a list instead of sending them.

Used by tests, and by development when no SMTP server is configured. Messages
contain sign-in links, so this is never used in production (Settings refuses).
"""

from __future__ import annotations

from app.domain.notifications import EmailMessage


class RecordingEmailProvider:
    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
