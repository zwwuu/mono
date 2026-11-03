# FileFlow — email notifications via SMTP (stdlib smtplib).
# If no SMTP host is configured, messages are written to the local outbox
# folder as .eml files so the flow still works end to end in demo setups.
import os
import smtplib
from email.message import EmailMessage

from ..config import env_secret


def send_email(cfg, to, subject, body):
    """Returns (ok: bool, how: str)."""
    smtp = cfg.raw.get("smtp", {})
    host = smtp.get("host", "")
    sender = smtp.get("from", "fileflow@localhost")
    recipients = [to] if isinstance(to, str) else list(to or [])
    if not recipients:
        recipients = cfg.get("notify", "recipients", [])
    if not recipients:
        return False, "no recipients configured"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if not host:
        outbox = cfg.path("outbox")
        os.makedirs(outbox, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in subject)[:60]
        path = os.path.join(outbox, f"{safe}.eml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
        return True, f"outbox file {path}"

    port = int(smtp.get("port", 587))
    security = smtp.get("security", "starttls")  # starttls | ssl | none
    username = smtp.get("username", "")
    password = env_secret("FILEFLOW_SMTP_PASSWORD") or smtp.get("password", "")
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    try:
        if security == "starttls":
            server.starttls()
        if username:
            server.login(username, password)
        server.send_message(msg)
    finally:
        server.quit()
    return True, f"smtp {host}:{port}"
