"""Adapters -- CLI and composition root (watcher).

This is the only place that knows both the use cases and the
concrete infrastructure classes. It builds the use cases from a
config file and hands them to the loop. Nothing else in the project
imports argparse, and no use case imports a database or the share
drive directly -- it goes through the repository and the filesystem
helpers.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..application.outbound import StageOutboundUseCase
from ..application.process import ProcessIncomingUseCase
from ..application.reconcile import ReconcileUseCase
from ..application.scan import ScanUseCase
from ..infrastructure.config import ConfigError, WatcherConfig, load_config
from ..infrastructure.filesystem import log, setup_logging
from ..infrastructure.persistence import SqlAlchemyRunRepository


def _open_repository(cfg: WatcherConfig) -> SqlAlchemyRunRepository:
    return SqlAlchemyRunRepository(Path(cfg.ledger_db))


def build_scan_use_case(cfg: WatcherConfig):
    """Compose one scan pass: (scan use case, repository).

    The caller owns the repository and must close it.
    """
    repository = _open_repository(cfg)
    scan = ScanUseCase(
        cfg,
        repository=repository,
        incoming=ProcessIncomingUseCase(cfg, repository=repository),
        outbound=StageOutboundUseCase(cfg, repository=repository),
    )
    return scan, repository


def build_reconcile_use_case(cfg: WatcherConfig):
    """Compose a reconcile run: (reconcile use case, repository)."""
    repository = _open_repository(cfg)
    return ReconcileUseCase(repository=repository), repository


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Watcher (FTP server)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--loop", type=int, default=0, help="poll every N seconds; 0 = scan once")
    ap.add_argument(
        "--reconcile",
        action="store_true",
        help="check ledger PUBLISHED runs against the share, then exit",
    )
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument(
        "--check-config",
        action="store_true",
        help="parse the config and exit: 0 when it is valid, 2 with the problems listed",
    )
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        # A broken config must fail loud here, before the loop or
        # the reconcile -- never as a per-run error, and never
        # retried once a minute in loop mode.
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if args.check_config:
        summary = (
            f"config OK: {len(cfg.jobs)} job(s) "
            f"({', '.join(sorted(cfg.jobs))}), "
            f"outbound {'on' if cfg.outbound is not None else 'off'}"
        )
        print(summary)
        return 0
    if args.reconcile:
        use_case, repository = build_reconcile_use_case(cfg)
        try:
            problems = use_case.run()
        finally:
            repository.close()
        if problems:
            for p in problems:
                log.error("reconcile: %s", p)
            return 1
        log.info("reconcile: share matches ledger, no problems")
        return 0
    while True:
        scan, repository = build_scan_use_case(cfg)
        try:
            counts = scan.run()
        finally:
            repository.close()
        if counts:
            log.info("scan: %s", counts)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
