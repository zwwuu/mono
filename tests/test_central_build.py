"""Tests for the central build dispatcher (loaded as a module)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("central_build", ROOT / "scripts" / "build.py")
build_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_module)


def test_discover_apps_lists_only_app_dirs_with_build_py(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    (apps / "alpha").mkdir(parents=True)
    (apps / "alpha" / "build.py").write_text("", encoding="utf-8")
    (apps / "beta-app").mkdir()
    (apps / "beta-app" / "build.py").write_text("", encoding="utf-8")
    (apps / "gamma").mkdir()
    (apps / "build.py").write_text("", encoding="utf-8")
    (apps / "notes.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(build_module, "APPS_DIR", str(apps))
    assert build_module.discover_apps() == ["alpha", "beta-app"]


def test_build_forwards_flags_to_app_script(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, check=False):
        seen.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    monkeypatch.setattr(build_module, "APPS_DIR", str(tmp_path))
    assert build_module.build("beta-app", clean=True, no_zip=True) == 0
    cmd = seen[0]
    assert cmd[0] == sys.executable
    assert cmd[1] == str(tmp_path / "beta-app" / "build.py")
    assert "--clean" in cmd and "--no-zip" in cmd


def test_build_returns_nonzero_without_stopping(monkeypatch):
    monkeypatch.setattr(
        build_module.subprocess,
        "run",
        lambda cmd, check=False: SimpleNamespace(returncode=3),
    )
    assert build_module.build("alpha", clean=False, no_zip=False) == 3


def test_main_reports_failures_and_continues(monkeypatch):
    monkeypatch.setattr(build_module, "discover_apps", lambda: ["alpha", "beta"])
    results = {"alpha": 0, "beta": 1}
    monkeypatch.setattr(build_module, "build", lambda name, clean, no_zip: results[name])
    with pytest.raises(SystemExit, match="failed: beta"):
        build_module.main(["all"])


def test_main_no_apps_found(monkeypatch):
    monkeypatch.setattr(build_module, "discover_apps", list)
    with pytest.raises(SystemExit, match="no apps found"):
        build_module.main(["all"])
