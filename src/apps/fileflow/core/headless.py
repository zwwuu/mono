"""Headless pipeline execution for the generated ff-* scenario apps.

Runs a resolved pipeline (from build_pipeline()) without FileFlow's
DB, dashboard, or scheduler. Each generated app embeds its scenario's
pipeline as pipeline.json and calls run_pipeline() with user-supplied
overrides (source, SMTP, recipients).

Headless differences from the engine, all logged:
- approval steps are skipped (no dashboard to click Approve on);
- run limits are not enforced (they are engine/DB state);
- step timeouts from the engine are not applied (per-step params
  like wait_processed's own timeout_seconds still are).
"""

import os
from pathlib import Path

from . import steps as steps_mod
from .steps import StepContext, StepError

__all__ = ["HeadlessConfig", "run_pipeline"]


class HeadlessConfig:
    """Minimal cfg shim satisfying what the step functions need.

    get(section, key, default): nested lookup in the raw dict.
    path(*parts): absolute paths pass through; relative ones resolve
        against the run workdir.
    raw: the full config dict (smtp/ftp/smb sections live here).
    """

    def __init__(self, workdir, raw=None):
        self._workdir = Path(workdir)
        self.raw = raw or {}

    def get(self, section, key, default=None):
        return self.raw.get(section, {}).get(key, default)

    def path(self, *parts):
        p = Path(*parts)
        if p.is_absolute():
            return str(p)
        return str(self._workdir / p)


def _adapt_log(log):
    """StepContext wants log(msg, level='INFO'); adapt a Logger."""

    def _log(msg, level="INFO"):
        getattr(log, level.lower(), log.info)(msg)

    return _log


def run_pipeline(steps, task, config, log, workdir):
    """Execute resolved pipeline steps headlessly. Returns an exit code.

    steps: [(key, label, stype, params)] as build_pipeline() returns.
    task: task dict (id, name, source, recipients, ...); the caller
        merges user overrides over the scenario's embedded values.
    config: raw config dict (smtp/ftp/smb/notify sections).
    log: a Logger. workdir: run working directory (created).
    """
    os.makedirs(workdir, exist_ok=True)
    cfg = HeadlessConfig(workdir, config)
    data = {}
    log_fn = _adapt_log(log)

    if task.get("run_limit"):
        log.warning("run limits are not enforced in headless mode")

    total = len(steps)
    for idx, (key, label, stype, params) in enumerate(steps):
        if stype == "approval":
            log.warning("step %d/%d [%s]: approval skipped (headless)", idx + 1, total, key)
            continue
        log.info("step %d/%d [%s] %s", idx + 1, total, key, label)
        ctx = StepContext(cfg, {"id": 1, "context": data}, log_fn, task, params, key)
        try:
            result = steps_mod.STEP_FUNCS[stype](ctx)
        except StepError as exc:
            log.error("step [%s] failed: %s", key, exc)
            return 1
        except Exception:
            log.exception("step [%s] crashed", key)
            return 1
        # ctx.data is the same dict object as data; keep the reference.
        log.info("step [%s]: %s", key, result)
    log.info("pipeline complete")
    return 0
