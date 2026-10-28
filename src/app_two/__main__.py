"""app-two entry point: file lister.

Absolute imports only — see app_one.__main__ for why.
"""

import argparse
from pathlib import Path

from app_two import APP_NAME, __version__
from common.logging import get_logger, setup_logging
from common.utils import app_file_path, load_app_config

DEFAULT_CONFIG = {
    "folder": ".",
    "pattern": "*.txt",
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_NAME, description="List files matching a pattern.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--folder", default=None, help="folder to list (overrides config)")
    parser.add_argument("--pattern", default=None, help="glob pattern (overrides config)")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ...")
    args = parser.parse_args(argv)

    setup_logging(level=args.log_level, name="app-two")
    log = get_logger("app-two")

    config = load_app_config(APP_NAME, defaults=DEFAULT_CONFIG)
    folder = Path(args.folder or config["folder"])
    pattern = args.pattern or config["pattern"]
    files = sorted(p for p in folder.glob(pattern) if p.is_file())
    log.info("%d file(s) in %s matching %r", len(files), folder, pattern)
    for path in files:
        log.info("  %s (%d bytes)", path.name, path.stat().st_size)

    sample = app_file_path(APP_NAME, "assets", "sample.txt")
    if sample:
        log.info("bundled asset found: %s", sample)
    else:
        log.warning("bundled asset sample.txt not found")

    # Template files with arbitrary names/folders, bundled via
    # EXTRA_DATAS in the app's build.py and located with app_file_path.
    for template in (
        ("templates", "monthly-summary.xlsx"),
        ("templates", "letters", "welcome-letter.docx"),
    ):
        path = app_file_path(APP_NAME, *template)
        if path:
            log.info("bundled template found: %s", path)
        else:
            log.warning("bundled template missing: %s", "/".join(template))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
