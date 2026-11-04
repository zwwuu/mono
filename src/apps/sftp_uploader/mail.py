"""Email notifications via SMTP (stdlib smtplib)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .log import log


def _login(server: smtplib.SMTP, ecfg: dict) -> None:
    username = ecfg.get("username")
    if username:
        server.login(username, ecfg.get("password") or "")


def send_email(cfg: dict, subject: str, body: str) -> bool:
    """Send one notification email. Returns True if sent, False if disabled.

    Raises on SMTP errors — the caller decides whether that is fatal.
    """
    ecfg = cfg.get("email", {}) or {}
    if not ecfg.get("enabled", True):
        log.info("email: disabled, skipping notification")
        return False

    msg = EmailMessage()
    msg["From"] = ecfg["from"]
    msg["To"] = ", ".join(ecfg["to"])
    msg["Subject"] = f"{ecfg.get('subject_prefix', '[sftp-uploader]')} {subject}"
    msg.set_content(body)

    host = ecfg["smtp_host"]
    port = int(ecfg.get("smtp_port", 587))
    timeout = 30

    log.debug("email: sending to %s subject=%r", msg["To"], msg["Subject"])
    if port == 465:
        # Implicit TLS.
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
            _login(server, ecfg)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            if ecfg.get("use_tls", True):
                server.starttls(context=ssl.create_default_context())
            _login(server, ecfg)
            server.send_message(msg)

    log.info("email: sent to %s", msg["To"])
    return True
