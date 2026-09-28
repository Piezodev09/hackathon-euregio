"""Outgoing e-mail.

Backends:
  * ``console`` - development: logged and kept in memory (tests read ``outbox``)
  * ``smtp``    - production with a mail server (TLS enforced, sent in the background)
  * ``none``    - self-hosting without a mail server: nothing is sent, the attempt is audited.
                  Invitation and reset links are then handed over by an admin (link / QR code).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from .config import Settings

log = logging.getLogger("mailer")


@dataclass
class Mail:
    to: str
    subject: str
    body: str


class Mailer:
    def __init__(self, settings: Settings, audit: Callable[..., None] | None = None):
        self.s = settings
        self.audit = audit
        self.outbox: list[Mail] = []  # 'console' only: for tests and local development
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.s.mail_backend != "none"

    def send(self, to: str, subject: str, body: str) -> bool:
        """Queue an e-mail. Returns False if no e-mail can be delivered (backend ``none``)."""
        mail = Mail(to, subject, body)
        if self.s.mail_backend == "none":
            # The body may contain secret links - never log it.
            if self.audit:
                self.audit("mail_not_sent", actor="system", target=to, detail={"subject": subject[:120]})
            return False
        if self.s.mail_backend == "smtp":
            # Send in the background so response times reveal nothing.
            threading.Thread(target=self._smtp, args=(mail,), daemon=True).start()
            return True
        with self._lock:
            self.outbox.append(mail)
            del self.outbox[:-100]
        log.info("E-mail (console) to %s: %s\n%s", to, subject, body)
        return True

    def _smtp(self, mail: Mail) -> None:
        msg = EmailMessage()
        msg["From"] = self.s.mail_from
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg.set_content(mail.body)
        try:
            ctx = ssl.create_default_context()
            if self.s.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, context=ctx, timeout=15)
            else:
                server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15)
                if self.s.smtp_starttls:
                    server.starttls(context=ctx)
            with server:
                if self.s.smtp_user:
                    server.login(self.s.smtp_user, self.s.smtp_password)
                server.send_message(msg)
        except Exception as exc:
            log.error("Sending e-mail to %s failed: %s", mail.to, type(exc).__name__)


def link(settings: Settings, route: str, token: str) -> str:
    # Token in the fragment (#): never sent to servers/proxies and never logged.
    return f"{settings.base_url}/app#/{route}?token={token}"
