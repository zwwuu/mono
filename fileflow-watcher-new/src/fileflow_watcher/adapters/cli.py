"""Adapters -- CLI and composition root (watcher).

This is the only place that knows both the use cases and the
concrete infrastructure classes. It builds the use cases from a
config file and hands them to the loop. Nothing else in the project
imports argparse, and no use case imports a database, a transport,
or the share drive directly -- it goes through the repository and
the transports.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..application.handoff import CompletionUseCase, HandoffUseCase
from ..application.outbound import StageOutboundUseCase
from ..application.reconcile import ReconcileUseCase
from ..application.scan import ScanUseCase
from ..application.stale import StaleAlertUseCase
from ..domain.ports import Transport
from ..infrastructure.config import ConfigError, WatcherConfig, load_config
from ..infrastructure.filesystem import log, setup_logging
from ..infrastructure.notify import Notifier
from ..infrastructure.persistence import SqlAlchemyRunRepository
from ..infrastructure.transport import build_transport


def _open_repository(cfg: WatcherConfig) -> SqlAlchemyRunRepository:
    return SqlAlchemyRunRepository(Path(cfg.ledger_db))


def build_scan_use_case(cfg: WatcherConfig):
    """Compose one scan pass: (scan use case, repository, transports).

    The caller owns the repository and the transports and must
    close them.
    """
    repository = _open_repository(cfg)
    intake = build_transport(cfg.intake.transport)
    processing = build_transport(cfg.processing.transport)
    notifier = Notifier(cfg.notify)
    scan = ScanUseCase(
        cfg,
        repository=repository,
        intake=intake,
        handoff=HandoffUseCase(cfg, repository=repository, intake=intake, processing=processing),
        completion=CompletionUseCase(
            cfg,
            repository=repository,
            intake=intake,
            processing=processing,
            notifier=notifier,
        ),
        outbound=StageOutboundUseCase(cfg, repository=repository, intake=intake),
        stale=(
            StaleAlertUseCase(cfg, repository=repository, notifier=notifier)
            if cfg.stale_after_seconds > 0
            else None
        ),
    )
    return scan, repository, (intake, processing)


def build_reconcile_use_case(cfg: WatcherConfig):
    """Compose a reconcile run: (reconcile use case, repository)."""
    repository = _open_repository(cfg)
    return ReconcileUseCase(repository=repository), repository


def _close_all(repository: SqlAlchemyRunRepository, transports: tuple[Transport, ...]) -> None:
    repository.close()
    for transport in transports:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Watcher (FTP 1 -> FTP 2 handoff)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--loop", type=int, default=0, help="poll every N seconds; 0 = scan once")
    ap.add_argument(
        "--reconcile",
        action="store_true",
        help="list runs still in flight (SENT_TO_PROCESSING), then exit",
    )
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument(
        "--check-config",
        action="store_true",
        help="parse the config and exit: 0 when it is valid, 2 with the problems listed",
    )
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        # A broken config must fail loud here, before the loop or
        # the reconcile -- never as a per-run error, and never
        # retried once a minute in loop mode.
        print(f"config error: {e}", file=sys.stderr)
        return 2
    setup_logging(args.log_level, log_file=cfg.log_file)
    if args.check_config:
        summary = (
            f"config OK: {len(cfg.jobs)} job(s) "
            f"({', '.join(cfg.jobs)}), "
            f"intake {cfg.intake.transport.kind}, "
            f"processing {cfg.processing.transport.kind}, "
            f"outbound {'on' if cfg.outbound is not None else 'off'}, "
            f"notify {'on' if cfg.notify is not None else 'off'}"
        )
        print(summary)
        return 0
    if args.reconcile:
        use_case, repository = build_reconcile_use_case(cfg)
        try:
            lines = use_case.run()
        finally:
            repository.close()
        if lines:
            for line in lines:
                log.error("reconcile: %s", line)
            return 1
        log.info("reconcile: nothing in flight")
        return 0
    while True:
        try:
            scan, repository, transports = build_scan_use_case(cfg)
        except Exception as e:  # noqa: BLE001 -- a failed scan pass must not kill the loop
            log.error("scan failed to start: %s", e)
            if not args.loop:
                return 1
            time.sleep(args.loop)
            continue
        try:
            counts = scan.run()
        except Exception as e:  # noqa: BLE001 -- a failed scan pass must not kill the loop
            log.error("scan failed: %s", e)
            counts = {}
        finally:
            _close_all(repository, transports)
        if counts:
            log.info("scan: %s", counts)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
