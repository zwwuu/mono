import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def discover_apps():
    """App names: every /<package>/build.py, sorted."""
    if not os.path.isdir(SCRIPTS_DIR):
        return []
    return sorted(
        entry.replace("_", "-")
        for entry in os.listdir(SCRIPTS_DIR)
        if os.path.isfile(os.path.join(SCRIPTS_DIR, entry, "build.py"))
    )


def build(name, clean, no_zip):
    """Run one app's build module; return its exit code."""
    package = name.replace("-", "_")
    cmd = [sys.executable, "-m", f"scripts.{package}.build"]
    if clean:
        cmd.append("--clean")

    print(f"=== {name} ===")

    return subprocess.run(cmd, check=False, cwd=ROOT).returncode


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
    parser.add_argument("--clean", action="store_true", help="clean before building")
    args = parser.parse_args(argv)

    names = apps if args.app == "all" else [args.app]
    if not names:
        raise SystemExit("no apps found under scripts/*/build.py")
    failures = [name for name in names if build(name, args.clean) != 0]
    if failures:
        raise SystemExit(f"failed: {', '.join(failures)}")
    print(f"done -> {os.path.join(ROOT, 'dist')}")


if __name__ == "__main__":
    main()
