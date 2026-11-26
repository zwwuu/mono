"""Infrastructure -- email notifications (stdlib smtplib).

One email per finished batch, sent by the completion use case.
The ``notify`` config section is optional: when it is absent the
notifier logs the message instead of sending it. A failed send
never fails the run it reports on -- it is logged and the scan
goes on.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .config import NotifyConfig
from .filesystem import log


class Notifier:
    def __init__(self, cfg: NotifyConfig | None):
        self.cfg = cfg

    def notify(self, subject: str, body: str) -> bool:
        """Send one notification email. True when sent, False when
        notify is not configured (logged instead) or sending failed.
        Never raises."""
        if self.cfg is None:
            log.info("notify (no email configured): %s\n%s", subject, body)
            return False
        cfg = self.cfg
        msg = EmailMessage()
        msg["From"] = cfg.from_addr
        msg["To"] = ", ".join(cfg.to)
        msg["Subject"] = f"[fileflow-watcher] {subject}"
        msg.set_content(body)
        try:
            if cfg.smtp_port == 465:
                # Implicit TLS.
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context) as server:
                    self._login(server, cfg)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
                    if cfg.use_tls:
                        server.starttls(context=ssl.create_default_context())
                    self._login(server, cfg)
                    server.send_message(msg)
        except Exception as e:  # noqa: BLE001 -- email failure is logged, never fatal
            log.error("notify: sending email failed: %s", e)
            return False
        log.info("notify: email sent to %s: %s", msg["To"], subject)
        return True

    @staticmethod
    def _login(server: smtplib.SMTP, cfg: NotifyConfig) -> None:
        if cfg.username:
            password = ""
            if cfg.password_env:
                password = os.environ.get(cfg.password_env, "")
            server.login(cfg.username, password)
