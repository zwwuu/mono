"""Tests for the central build dispatcher (loaded as a module)."""

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("central_build", ROOT / "build.py")
build_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_module)


def test_discover_apps_lists_only_package_dirs_with_build_py(tmp_path, monkeypatch):
    apps = tmp_path / "src" / "apps"
    (apps / "alpha").mkdir(parents=True)
    (apps / "alpha" / "build.py").write_text("", encoding="utf-8")
    (apps / "beta_app").mkdir()
    (apps / "beta_app" / "build.py").write_text("", encoding="utf-8")
    (apps / "gamma").mkdir()  # no build.py -> skipped
    (apps / "notes.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(build_module, "APPS_DIR", str(apps))
    assert build_module.discover_apps() == ["alpha", "beta-app"]


def test_build_forwards_flags_to_app_script(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, check=False, cwd=None, env=None):
        seen.append((cmd, cwd, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    assert build_module.build("beta-app", no_zip=True) == 0
    cmd, cwd, env = seen[0]
    assert cmd == [sys.executable, "-m", "apps.beta_app.build", "--no-zip"]
    # cwd=ROOT is where PyInstaller's default dist/build folders land.
    assert cwd == build_module.ROOT
    # PYTHONPATH=src is what lets the per-app module (and PyInstaller)
    # import apps.* and common.* with no install.
    assert env["PYTHONPATH"].split(os.pathsep)[0] == build_module.SRC_DIR


def test_build_returns_nonzero_without_stopping(monkeypatch):
    monkeypatch.setattr(
        build_module.subprocess,
        "run",
        lambda cmd, check=False, **kwargs: SimpleNamespace(returncode=3),
    )
    assert build_module.build("alpha", no_zip=False) == 3


def test_main_reports_failures_and_continues(monkeypatch):
    monkeypatch.setattr(build_module, "discover_apps", lambda: ["alpha", "beta"])
    results = {"alpha": 0, "beta": 1}
    monkeypatch.setattr(build_module, "build", lambda name, no_zip: results[name])
    with pytest.raises(SystemExit, match="failed: beta"):
        build_module.main(["all"])


def test_main_no_apps_found(monkeypatch):
    monkeypatch.setattr(build_module, "discover_apps", list)
    with pytest.raises(SystemExit, match="no apps found"):
        build_module.main(["all"])
