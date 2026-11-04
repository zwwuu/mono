"""Build all apps, or one app by name, with PyInstaller.

Usage (from anywhere):
    python build.py                 # build every app
    python build.py app-one         # build just app-one
    python build.py app-one --no-zip

Each app's build script (src/apps/<package>/build.py) is a small
subclass of the default build config in
src/common/build_config/build_config.py; this script just discovers
and runs them as modules (`python -m apps.<package>.build`).
Apps are discovered by scanning src/apps/*/build.py, so adding a
new app needs no edit here. The app name is the package dir with
underscores turned into hyphens (app_one -> app-one), following
the repo's naming convention. --no-zip is forwarded to the
per-app scripts; to build one app with other options, run its
build module directly from the repo root.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT, "src")
APPS_DIR = os.path.join(SRC_DIR, "apps")


def discover_apps():
    """App names: every src/apps/<package>/build.py, sorted."""
    if not os.path.isdir(APPS_DIR):
        return []
    return sorted(
        entry.replace("_", "-")
        for entry in os.listdir(APPS_DIR)
        if os.path.isfile(os.path.join(APPS_DIR, entry, "build.py"))
    )


def build(name, no_zip):
    """Run one app's build module; return its exit code."""
    package = name.replace("-", "_")
    cmd = [sys.executable, "-m", f"apps.{package}.build"]
    if no_zip:
        cmd.append("--no-zip")
    print(f"=== {name} ===")
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + pythonpath if pythonpath else "")
    # check=False on purpose: build-all continues past a failed app and
    # reports all failures at the end instead of stopping at the first.
    # cwd=ROOT is where PyInstaller's default dist/build folders land;
    # PYTHONPATH=src is what lets the per-app module (and PyInstaller)
    # import apps.* and common.* with no install.
    return subprocess.run(cmd, check=False, cwd=ROOT, env=env).returncode


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
    parser.add_argument("--no-zip", action="store_true", help="skip the zip bundle")
    args = parser.parse_args(argv)

    names = apps if args.app == "all" else [args.app]
    if not names:
        raise SystemExit("no apps found under src/apps/*/build.py")
    failures = [name for name in names if build(name, args.no_zip) != 0]
    if failures:
        raise SystemExit(f"failed: {', '.join(failures)}")
    print(f"done -> {os.path.join(ROOT, 'dist')}")


if __name__ == "__main__":
    main()
