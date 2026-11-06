"""SMTP delivery via aiosmtplib. In development, point it at Mailpit."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage

import aiosmtplib

from app.domain.notifications import EmailDeliveryError, EmailMessage


class SmtpEmailProvider:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._use_starttls = use_starttls

    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                start_tls=self._use_starttls,
                timeout=10,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(f"SMTP delivery to {self._host} failed") from exc
