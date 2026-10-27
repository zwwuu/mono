"""Wire the note-indexing stages together: arguments, config, output."""

import argparse

from app_five import APP_NAME, __version__
from app_five.index import build_index
from app_five.parser import parse_note
from app_five.scanner import find_notes
from common.logging import get_logger, setup_logging
from common.utils import load_app_config, save_json

DEFAULT_CONFIG = {"folder": ".", "pattern": "*.md", "output": "notes-index.json"}


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Index a folder of Markdown notes.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--folder", default=None, help="folder of notes to index")
    parser.add_argument("--pattern", default=None, help="filename glob (default: *.md)")
    parser.add_argument(
        "--config-dir", default=None, help="directory holding config.json (overrides)"
    )
    parser.add_argument("--log-level", default=None, help="log level (default: INFO)")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    log = get_logger(APP_NAME)
    search_dirs = [args.config_dir] if args.config_dir else None
    config = load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)
    folder = args.folder or config["folder"]
    pattern = args.pattern or config["pattern"]

    notes = find_notes(folder, pattern)
    records = [parse_note(path) for path in notes]
    index = build_index(records)
    save_json(config["output"], index)
    if notes:
        log.info(
            "indexed %d notes (%d words) -> %s", len(notes), index["total_words"], config["output"]
        )
    else:
        log.info("no notes found in %s, wrote an empty index -> %s", folder, config["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
