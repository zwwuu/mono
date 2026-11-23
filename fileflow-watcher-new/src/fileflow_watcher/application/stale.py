"""Application -- stuck-file alert.

A file the processor silently drops sits in SENT_TO_PROCESSING
forever; reconcile only reports it when someone runs it. This
use case runs at the end of every scan: any file in a
SENT_TO_PROCESSING run that has waited longer than
``stale_after_seconds`` (default 86400 = 24h, 0 disables) gets
ONE alert email naming the stuck file, job, run_id, and wait
time. The alert is recorded per file (``stale_alerted`` in the
run detail) so the scan does not spam every pass; the flag is
set only when the email was actually sent, so a failed send
retries on the next scan.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.ports import RunRepository
from ..infrastructure.config import WatcherConfig
from ..infrastructure.filesystem import log
from ..infrastructure.notify import Notifier
from .reconcile import _fmt_wait


class StaleAlertUseCase:
    def __init__(
        self,
        cfg: WatcherConfig,
        *,
        repository: RunRepository,
        notifier: Notifier,
    ):
        self.cfg = cfg
        self.repository = repository
        self.notifier = notifier

    def run(self, now: datetime | None = None) -> int:
        """Alert on stuck files. Returns the number of alerts sent."""
        threshold = self.cfg.stale_after_seconds
        if threshold <= 0:
            return 0
        now = now or datetime.now(timezone.utc)
        sent = 0
        for run in self.repository.list_by_state("SENT_TO_PROCESSING"):
            files = run.detail.get("files", [])
            outcomes = run.detail.get("outcomes", {})
            alerted = set(run.detail.get("stale_alerted", []))
            sent_at = run.detail.get("sent_at") or run.updated_at.isoformat()
            for f in files:
                name = f.get("name")
                if name is None or name in outcomes or name in alerted:
                    continue
                try:
                    sent_dt = datetime.fromisoformat(sent_at)
                except ValueError:
                    continue
                if sent_dt.tzinfo is None:
                    sent_dt = sent_dt.replace(tzinfo=timezone.utc)
                waited = (now - sent_dt).total_seconds()
                if waited <= threshold:
                    continue
                subject = f"{run.job} {run.run_id}: stuck file {name}"
                body = (
                    f"job: {run.job}\n"
                    f"run: {run.run_id}\n"
                    f"file: {name}\n"
                    f"waiting: {_fmt_wait(waited)}\n"
                    f"sent to processing: {sent_at}"
                )
                try:
                    sent_now = self.notifier.notify(subject, body)
                except Exception as e:  # noqa: BLE001 -- a dead mail server must not fail the scan
                    log.error("stale alert for run %s %s failed: %s", run.run_id, name, e)
                    continue
                if not sent_now:
                    # Not sent -- either no notify section (an error
                    # line, not silence) or a failed send. Either way
                    # the flag stays unset, so the next scan retries.
                    if self.notifier.cfg is None:
                        log.error(
                            "stale alert: no notify section configured; run %s %s stuck for %s",
                            run.run_id,
                            name,
                            _fmt_wait(waited),
                        )
                    continue
                alerted.add(name)
                self.repository.set_state(
                    run.run_id, "SENT_TO_PROCESSING", stale_alerted=sorted(alerted)
                )
                log.warning(
                    "run %s: %s stuck for %s, alert sent",
                    run.run_id,
                    name,
                    _fmt_wait(waited),
                )
                sent += 1
        return sent
