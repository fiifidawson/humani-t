"""The outbound email seam."""

from __future__ import annotations

from typing import Protocol

from app.domain.notifications import EmailMessage


class EmailProvider(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Raise `EmailDeliveryError` when the message could not be handed over."""
        ...
