"""Adapters -- CLI and composition root (agent).

This is the only place that knows both the use cases and the
concrete infrastructure classes. It builds a use case from a config
file (transport + repository + source/uploader) and hands it to the
loop. Nothing else in the project imports argparse, and no use case
imports a transport, a database, or the website.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..application.download import DownloadAndUploadUseCase
from ..application.pickup import PickupUseCase
from ..infrastructure.filesystem import log, setup_logging
from ..infrastructure.persistence import SqlAlchemyRunRepository
from ..infrastructure.sources import build_source
from ..infrastructure.transport import build_transport
from ..infrastructure.website import WebsiteUploader


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_download_use_case(cfg: dict):
    """Compose one forward run: (use case, transport, repository).

    The caller owns the transport and repository and must close them.
    """
    transport = build_transport(cfg["ftp"])
    repository = SqlAlchemyRunRepository(Path(cfg["ledger_db"]))
    use_case = DownloadAndUploadUseCase(
        cfg, source=build_source(cfg["source"]), transport=transport, repository=repository
    )
    return use_case, transport, repository


def build_pickup_use_case(cfg: dict):
    """Compose one pickup run: (use case, transport, repository)."""
    transport = build_transport(cfg["ftp"])
    repository = SqlAlchemyRunRepository(Path(cfg["ledger_db"]))
    use_case = PickupUseCase(
        cfg, transport=transport, repository=repository, uploader=WebsiteUploader(cfg["pickup"])
    )
    return use_case, transport, repository


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
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config(args.config)
    while True:
        failed = False
        try:
            if args.mode in ("download", "both"):
                use_case, transport, repository = build_download_use_case(cfg)
                try:
                    use_case.run()
                finally:
                    transport.close()
                    repository.close()
            if args.mode in ("pickup", "both"):
                use_case, transport, repository = build_pickup_use_case(cfg)
                try:
                    counts = use_case.run()
                    if counts:
                        log.info("pickup: %s", counts)
                finally:
                    transport.close()
                    repository.close()
        except Exception as e:  # noqa: BLE001 -- a failed run must not kill the loop
            log.error("agent run failed: %s", e)
            failed = True
        if not args.loop:
            return 1 if failed else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
