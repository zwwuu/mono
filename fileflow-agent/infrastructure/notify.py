"""Infrastructure -- email notification on run completion.

One email per run, sent when the run finishes: UPLOADED (or
UPLOADED_TO_WEBSITE in pickup mode) and FAILED. SKIPPED sends
nothing -- otherwise every --loop tick with nothing new would
spam the operator. When the config has no ``notify`` section the
notifier only logs, so a config without email still works.

Stdlib smtplib only. STARTTLS on port 587 (the submission port);
other ports send plain. The SMTP password comes from the env var
named in ``password_env`` -- never from the config file -- and
the login username is the sender address. A failed notification
never fails the run: send errors are logged, never raised.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from .config import NotifyConfig
from .filesystem import log


class SmtpNotifier:
    """Sends one email per completed run; logs instead when the
    config has no ``notify`` section, and never raises."""

    def __init__(self, cfg: NotifyConfig | None = None, *, smtp_factory=None):
        self.cfg = cfg
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def notify(
        self,
        *,
        job: str,
        run_id: str,
        status: str,
        files: list[str],
        destination: str | None = None,
        error: str | None = None,
        summary: str | None = None,
    ) -> None:
        if self.cfg is None:
            log.info("notify: no notify section; run %s %s (not emailed)", run_id, status)
            return
        subject = f"[fileflow-agent] {job} {run_id} {status}"
        lines = [f"job: {job}", f"run_id: {run_id}", f"status: {status}"]
        if summary:
            lines.append(f"summary: {summary}")
        if destination:
            lines.append(f"destination: {destination}")
        if files:
            lines.append("files:")
            lines.extend(f"  - {name}" for name in files)
        if error:
            lines.append(f"error: {error}")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.from_addr
        msg["To"] = self.cfg.to
        msg.set_content("\n".join(lines) + "\n")
        try:
            self._send(msg)
        except (smtplib.SMTPException, OSError) as e:
            # Notification is best-effort: a dead mail server must
            # not turn a successful run into a failed one.
            log.error("notify: email for run %s failed: %s", run_id, e)

    def _send(self, msg: EmailMessage) -> None:
        cfg = self.cfg
        with self._smtp_factory(cfg.smtp_host, cfg.smtp_port) as server:
            if cfg.smtp_port == 587:
                try:
                    server.starttls()
                except smtplib.SMTPException as e:
                    log.warning("notify: STARTTLS failed (%s); sending plain", e)
            password = os.environ.get(cfg.password_env) if cfg.password_env else None
            if password:
                server.login(cfg.from_addr, password)
            server.send_message(msg)
