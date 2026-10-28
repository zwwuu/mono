"""Build all apps, or one app by name, with PyInstaller.

Usage (from anywhere):
    python scripts/build.py                 # build every app
    python scripts/build.py all             # same
    python scripts/build.py app-one         # build just app-one
    python scripts/build.py all --clean
    python scripts/build.py app-one --no-zip

Each app's build script (apps/<app>/build.py) is a small
subclass of the default build config in scripts/build_config.py; this
script just discovers and runs them. Apps are discovered by scanning
apps/*/build.py, so adding a new app needs no edit here. The app
name is the app folder's name (apps/app-one -> app-one).
Options are forwarded to the per-app scripts; for anything else
(e.g. --name, --distpath) run the app's build script directly.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_DIR = os.path.join(ROOT, "apps")


def discover_apps():
    """App names: every apps/<app>/build.py, sorted."""
    if not os.path.isdir(APPS_DIR):
        return []
    return sorted(
        entry
        for entry in os.listdir(APPS_DIR)
        if os.path.isfile(os.path.join(APPS_DIR, entry, "build.py"))
    )


def build(name, clean, no_zip):
    """Run one app's build script; return its exit code."""
    cmd = [sys.executable, os.path.join(APPS_DIR, name, "build.py")]
    if clean:
        cmd.append("--clean")
    if no_zip:
        cmd.append("--no-zip")
    print(f"=== {name} ===")
    # check=False on purpose: build-all continues past a failed app and
    # reports all failures at the end instead of stopping at the first.
    return subprocess.run(cmd, check=False).returncode


def main(argv=None):
    apps = discover_apps()
    parser = argparse.ArgumentParser(description="Build monorepo apps with PyInstaller.")
    parser.add_argument(
        "app",
        nargs="?",
        default="all",
        choices=["all", *apps],
        help="app to build (default: all)",
    )
    parser.add_argument("--clean", action="store_true", help="wipe PyInstaller's build cache first")
    parser.add_argument("--no-zip", action="store_true", help="skip the zip bundle")
    args = parser.parse_args(argv)

    names = apps if args.app == "all" else [args.app]
    if not names:
        raise SystemExit("no apps found under apps/*/build.py")
    failures = [name for name in names if build(name, args.clean, args.no_zip) != 0]
    if failures:
        raise SystemExit(f"failed: {', '.join(failures)}")
    print(f"done -> {os.path.join(ROOT, 'dist')}")


if __name__ == "__main__":
    main()
