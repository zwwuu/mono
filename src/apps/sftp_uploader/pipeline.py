"""Run the steps in order, with retry, failure attribution, and notifications.

To add a step: subclass :class:`sftp_uploader.steps.Step` (set ``name``,
implement ``run``) and register it on the pipeline::

    pipeline = Pipeline(config)
    pipeline.steps.insert(2, ChecksumStep())

Retry, logging, step-failure attribution, and both notification emails pick
the new step up automatically.
"""
from __future__ import annotations

import os
import time
import traceback

from . import mail
from .context import RunContext
from .log import log
from .steps import ConnectStep, FindFileStep, Step, UploadStep, VerifyStep


class Notifier:
    """Builds and sends the success / failure notification emails."""

    def __init__(self, config):
        self.config = config

    def success(self, ctx: RunContext, attempts: int, elapsed: float) -> None:
        remote = ctx.remote_path or (self.config["sftp"].get("remote_dir") or "(login dir)").rstrip("/") or "/"
        if ctx.skipped:
            subject = f"SKIPPED: {ctx.filename} already on server"
            body = (
                f"{ctx.filename} already exists at {self.config['sftp']['host']}:{remote}\n"
                f"not re-uploaded (on_existing=skip), attempt {attempts}\n"
            )
        else:
            size = os.path.getsize(ctx.local_path)
            subject = f"OK: uploaded {ctx.filename}"
            body = (
                f"Uploaded {ctx.filename} ({size} bytes)\n"
                f"to {self.config['sftp']['host']}:{remote}\n"
                f"in {elapsed:.1f}s, attempt {attempts}\n"
            )
        try:
            mail.send_email(self.config, subject, body)
        except Exception as exc:  # noqa: BLE001 — the transfer outcome stands
            log.warning("notification email could not be sent: %s", exc)

    def failure(self, step: str, exc: BaseException, attempts: int, filename: str | None) -> None:
        body = (
            f"Upload FAILED at step '{step}' after {attempts} attempt(s).\n"
            f"file: {filename or '(not found yet)'}\n"
            f"error: {type(exc).__name__}: {exc}\n"
        )
        try:
            mail.send_email(self.config, f"FAILED at step '{step}' after {attempts} attempt(s)", body)
        except Exception as send_exc:  # noqa: BLE001 — report, don't mask the original failure
            log.warning("failure email could not be sent: %s", send_exc)


class Pipeline:
    """Runs :class:`Step` objects in order with retry and notifications."""

    step_classes: tuple[type[Step], ...] = (FindFileStep, ConnectStep, UploadStep, VerifyStep)

    def __init__(self, config):
        self.config = config
        self.steps: list[Step] = [cls() for cls in self.step_classes]
        self.notifier = Notifier(config)

    @staticmethod
    def _close_connections(ctx: RunContext) -> None:
        for key in ("sftp", "ssh"):
            conn = getattr(ctx, key, None)
            if conn is None:
                continue
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best effort on a broken connection
                pass
            finally:
                setattr(ctx, key, None)

    def execute(self) -> int:
        """Run the pipeline with retries. Returns a process exit code:

        0 — uploaded (and verified) successfully, or skipped by policy
        1 — failed after all attempts (failure email names the step)
        3 — no local file matched the pattern after all attempts
        """
        rcfg = self.config.get("retry", {}) or {}
        max_attempts = max(1, int(rcfg.get("max_attempts", 3)))
        backoff = float(rcfg.get("backoff_seconds", 10))
        backoff_cap = float(rcfg.get("backoff_max_seconds", 300))

        started = time.monotonic()
        last_step: str | None = None
        last_exc: BaseException | None = None
        filename: str | None = None

        for attempt in range(1, max_attempts + 1):
            ctx = RunContext(self.config, attempt)
            log.debug("attempt %d/%d starting — effective config: %s",
                      attempt, max_attempts, self.config.redact())
            try:
                for step in self.steps:
                    ctx.step = step.name
                    log.info("attempt %d/%d — step: %s", attempt, max_attempts, step.name)
                    t_step = time.monotonic()
                    step.run(ctx)
                    log.debug("attempt %d/%d — step '%s' finished in %.2fs",
                              attempt, max_attempts, step.name, time.monotonic() - t_step)
                elapsed = time.monotonic() - started
                if ctx.skipped:
                    log.info("done: %s already on server — skipped (attempt %d/%d)",
                             ctx.filename, attempt, max_attempts)
                else:
                    log.info("done: %s uploaded in %.1fs (attempt %d/%d)",
                             ctx.filename, elapsed, attempt, max_attempts)
                self._close_connections(ctx)
                self.notifier.success(ctx, attempt, elapsed)
                return 0
            except Exception as exc:  # noqa: BLE001 — any step failure is retried/reported
                last_step, last_exc = ctx.step or "?", exc
                filename = ctx.filename
                log.error("attempt %d/%d FAILED at step '%s': %s: %s",
                          attempt, max_attempts, last_step, type(exc).__name__, exc)
                log.debug("traceback:\n%s", traceback.format_exc())
                self._close_connections(ctx)
                if attempt < max_attempts:
                    delay = min(backoff_cap, backoff * 2 ** (attempt - 1))
                    log.info("retrying in %.0fs ...", delay)
                    time.sleep(delay)

        # All attempts exhausted.
        assert last_step is not None and last_exc is not None
        self.notifier.failure(last_step, last_exc, max_attempts, filename)
        if last_step == "find_file" and isinstance(last_exc, FileNotFoundError):
            return 3
        return 1
