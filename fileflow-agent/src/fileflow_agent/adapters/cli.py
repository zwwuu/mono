"""Adapters -- CLI and composition root (agent).

This is the only place that knows both the use cases and the
concrete infrastructure classes. It builds a use case from a config
file (transport + repository + source/uploader) and hands it to the
loop. Nothing else in the project imports argparse, and no use case
imports a transport, a database, or the website.
"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from ..application.download import DownloadAndUploadUseCase
from ..application.pickup import PickupUseCase
from ..domain.integrity import Context
from ..domain.ports import Notifier
from ..infrastructure.config import AgentConfig, ConfigError, load_config
from ..infrastructure.filesystem import log, setup_logging
from ..infrastructure.notify import SmtpNotifier
from ..infrastructure.persistence import SqlAlchemyRunRepository
from ..infrastructure.sources import build_source
from ..infrastructure.transport import build_transport
from ..infrastructure.website import WebsiteUploader


def _context(cfg: AgentConfig) -> Context:
    """One Context per tick: the date in every local path is this
    tick's date, not the process's startup date. The builders run
    inside the loop, so each tick gets a fresh one."""
    return Context(cfg.job, cfg.category, cfg.variables, cfg.date_format, cfg.time_format)


def build_download_use_case(cfg: AgentConfig, *, notifier: Notifier | None = None):
    """Compose one forward run: (use case, transport, repository).

    The caller owns the transport and repository and must close them.
    The config was parsed at startup, so ``destination`` is present;
    a config without ``source`` cannot run in download mode. A
    notifier is built from the config's ``notify`` section unless
    the caller passes one (tests inject a fake SMTP)."""
    if cfg.source is None:
        raise ValueError("config has no 'source' section; download mode needs one")
    transport = build_transport(cfg.destination.transport)
    repository = SqlAlchemyRunRepository(Path(_context(cfg).local_path(cfg.ledger_db)))
    use_case = DownloadAndUploadUseCase(
        cfg,
        source=build_source(
            cfg.source, cfg.job, cfg.category, cfg.variables, cfg.date_format, cfg.time_format
        ),
        transport=transport,
        repository=repository,
        notifier=notifier if notifier is not None else SmtpNotifier(cfg.notify),
    )
    return use_case, transport, repository


def build_pickup_use_case(cfg: AgentConfig, *, notifier: Notifier | None = None):
    """Compose one pickup run: (use case, transport, repository).

    The uploader is None when the config has no ``deliver`` section;
    the use case then does nothing."""
    transport = build_transport(cfg.destination.transport)
    repository = SqlAlchemyRunRepository(Path(_context(cfg).local_path(cfg.ledger_db)))
    uploader = WebsiteUploader(cfg.deliver) if cfg.deliver is not None else None
    use_case = PickupUseCase(
        cfg,
        transport=transport,
        repository=repository,
        uploader=uploader,
        notifier=notifier if notifier is not None else SmtpNotifier(cfg.notify),
    )
    return use_case, transport, repository


@contextmanager
def _opened(build):
    """Build a use case, yield it, and always close its transport
    and repository -- the caller owns neither after this."""
    use_case, transport, repository = build()
    try:
        yield use_case
    finally:
        transport.close()
        repository.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Agent (Ethernet PC)")
    ap.add_argument("--config", required=True)
    ap.add_argument(
        "--mode",
        choices=["download", "pickup", "both"],
        default="download",
        help="download: website -> FTP (default); pickup: FTP outgoing"
        " -> website; both: run download then pickup",
    )
    ap.add_argument("--loop", type=int, default=0, help="repeat every N seconds; 0 = run once")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument(
        "--check-config",
        action="store_true",
        help="parse the config and exit: 0 when it is valid, 2 with the first problem named",
    )
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        # A broken config must fail loud here, before the loop --
        # never retry once a minute and log the same error forever.
        print(f"config error: {e}", file=sys.stderr)
        return 2
    # Logging is set up after the config loads, so the config's
    # log_file (when set) takes effect from the first run. log_file
    # is a template: it is resolved here and again each tick, so a
    # date-partitioned log follows the day, not the startup day.
    setup_logging(
        args.log_level,
        log_file=_context(cfg).local_path(cfg.log_file) if cfg.log_file else None,
    )
    if args.check_config:
        dest = cfg.destination.transport
        where = dest.root if dest.kind == "local" else f"{dest.host}:{dest.port}"
        summary = (
            f"config OK: job {cfg.job!r}, source {cfg.source.type}, destination {dest.kind} {where}"
        )
        if cfg.deliver is not None:
            summary += f", deliver {cfg.deliver.upload_url}"
        print(summary)
        return 0
    if args.mode in ("download", "both") and cfg.source is None:
        # Same class of failure as a bad config: fail loud here,
        # before the loop, never as a per-run error.
        print("config error: download mode needs a 'source' section", file=sys.stderr)
        return 2
    while True:
        failed = False
        try:
            if cfg.log_file:
                setup_logging(args.log_level, log_file=_context(cfg).local_path(cfg.log_file))
            if args.mode in ("download", "both"):
                with _opened(lambda: build_download_use_case(cfg)) as use_case:
                    use_case.run()
            if args.mode in ("pickup", "both"):
                with _opened(lambda: build_pickup_use_case(cfg)) as use_case:
                    counts = use_case.run()
                    if counts:
                        log.info("pickup: %s", counts)
        except Exception as e:  # noqa: BLE001 -- a failed run must not kill the loop
            log.error("agent run failed: %s", e)
            failed = True
        if not args.loop:
            return 1 if failed else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
