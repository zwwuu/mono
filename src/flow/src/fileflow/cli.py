# FileFlow — command-line entry point.
#
# Installed as the `fileflow` console script (see pyproject.toml), and
# runnable without installing via `python -m fileflow`.
import argparse

from . import __version__
from .app import App


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fileflow",
        description="FileFlow task runner: download, approve, process, "
        "archive, distribute — on demand or on a schedule.",
    )
    ap.add_argument("--version", action="version", version=f"fileflow {__version__}")
    ap.add_argument(
        "--config",
        default=None,
        help="path to config.json (default: $FILEFLOW_CONFIG or ./config.json)",
    )
    ap.add_argument("--host", default=None, help="dashboard host (overrides config)")
    ap.add_argument("--port", type=int, default=None, help="dashboard port (overrides config)")
    args = ap.parse_args(argv)

    app = App.from_config(args.config)
    app.start()
    host = args.host or app.cfg.get("app", "host", "127.0.0.1")
    port = args.port or int(app.cfg.get("app", "port", 8080))
    from loguru import logger

    logger.info(
        "[scheduler] running — daily/weekly/monthly schedules fire automatically; "
        "past-due schedules fire once on startup (catch-up)"
    )
    app.serve(host, port)


if __name__ == "__main__":
    main()
