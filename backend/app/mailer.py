"""E-Mail-Versand: 'console' für Entwicklung (Log + Speicher), 'smtp' für den Betrieb (TLS erzwungen)."""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("mailer")


@dataclass
class Mail:
    to: str
    subject: str
    body: str


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings
        self.outbox: list[Mail] = []  # nur 'console': für Tests und lokale Entwicklung
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, body: str) -> None:
        mail = Mail(to, subject, body)
        if self.s.mail_backend == "smtp":
            # Versand im Hintergrund, damit Antwortzeiten keine Rückschlüsse erlauben.
            threading.Thread(target=self._smtp, args=(mail,), daemon=True).start()
            return
        with self._lock:
            self.outbox.append(mail)
            del self.outbox[:-100]
        log.info("E-Mail (console) an %s: %s\n%s", to, subject, body)

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
            log.error("E-Mail-Versand an %s fehlgeschlagen: %s", mail.to, type(exc).__name__)


def link(settings: Settings, route: str, token: str) -> str:
    # Token im Fragment (#): wird nicht an Server/Proxys übertragen und landet nicht in Logs.
    return f"{settings.base_url}/app#/{route}?token={token}"
