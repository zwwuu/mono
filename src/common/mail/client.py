"""Minimal SMTP mail client shared by all apps (stdlib smtplib only).

No SMTP host configured -> dry-run mode: messages are logged, not
sent, and send() still returns True. That keeps every app runnable
without secrets, and makes the mail path testable.

Secrets never live in app config files: use MailConfig.from_env()
(MAIL_HOST, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM,
MAIL_USE_TLS / MAIL_USE_SSL) or pass a MailConfig explicitly.
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from common.utils.misc import getenv, getenv_bool, getenv_int

__all__ = ["MailClient", "MailConfig"]


@dataclass
class MailConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True  # STARTTLS (port 587); set False with use_ssl for 465
    use_ssl: bool = False  # implicit TLS (port 465)
    from_addr: str = ""
    timeout: int = 30

    @classmethod
    def from_env(cls, prefix="MAIL_"):
        """Build a config from MAIL_* environment variables."""
        return cls(
            host=getenv(f"{prefix}HOST", ""),
            port=getenv_int(f"{prefix}PORT", 587),
            username=getenv(f"{prefix}USERNAME", ""),
            password=getenv(f"{prefix}PASSWORD", ""),
            use_tls=getenv_bool(f"{prefix}USE_TLS", True),
            use_ssl=getenv_bool(f"{prefix}USE_SSL", False),
            from_addr=getenv(f"{prefix}FROM", ""),
            timeout=getenv_int(f"{prefix}TIMEOUT", 30),
        )


class MailClient:
    """Send mail; dry_run=True (or no host) only logs the message."""

    def __init__(self, config=None, dry_run=None, logger=None):
        self.config = config or MailConfig()
        # Dry-run unless explicitly disabled AND a host is configured.
        self.dry_run = self.config.host == "" if dry_run is None else dry_run
        self.log = logger or logging.getLogger(__name__)

    def send(self, to, subject, body, html=None, attachments=None):
        """Send one message. `to` is an address or a list. Returns bool.

        Attachments: paths (str/Path); each is attached by filename.
        In dry-run mode nothing is read or sent: the message is only
        logged, so a missing attachment file cannot break a dry run.
        """
        recipients = [to] if isinstance(to, str) else list(to or [])
        if not recipients:
            self.log.error("mail: no recipients, not sending")
            return False
        if self.dry_run:
            self.log.info(
                "mail dry-run: to=%s subject=%r body=%d chars attachments=%d",
                recipients,
                subject,
                len(body),
                len(attachments or []),
            )
            return True
        msg = EmailMessage()
        msg["From"] = self.config.from_addr or self.config.username or "noreply@localhost"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        for path in attachments or []:
            path = Path(path)
            msg.add_attachment(
                path.read_bytes(),
                maintype="application",
                subtype="octet-stream",
                filename=path.name,
            )
        try:
            if self.config.use_ssl:
                server = smtplib.SMTP_SSL(
                    self.config.host, self.config.port, timeout=self.config.timeout
                )
            else:
                server = smtplib.SMTP(
                    self.config.host, self.config.port, timeout=self.config.timeout
                )
            try:
                if not self.config.use_ssl and self.config.use_tls:
                    server.starttls()
                if self.config.username:
                    server.login(self.config.username, self.config.password)
                server.send_message(msg)
            finally:
                server.quit()
        except (smtplib.SMTPException, OSError) as e:
            self.log.error("mail send failed: %s", e)
            return False
        self.log.info("mail sent: to=%s subject=%r", recipients, subject)
        return True
