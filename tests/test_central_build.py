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


def test_discover_apps_lists_only_package_dirs_with_build_py(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    (scripts / "alpha").mkdir(parents=True)
    (scripts / "alpha" / "build.py").write_text("", encoding="utf-8")
    (scripts / "beta_app").mkdir()
    (scripts / "beta_app" / "build.py").write_text("", encoding="utf-8")
    (scripts / "gamma").mkdir()
    (scripts / "build.py").write_text("", encoding="utf-8")
    (scripts / "build_config.py").write_text("", encoding="utf-8")
    (scripts / "notes.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(build_module, "SCRIPTS_DIR", str(scripts))
    assert build_module.discover_apps() == ["alpha", "beta-app"]


def test_build_forwards_flags_to_app_script(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, check=False, cwd=None):
        seen.append((cmd, cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    monkeypatch.setattr(build_module, "SCRIPTS_DIR", str(tmp_path))
    assert build_module.build("beta-app", clean=True, no_zip=True) == 0
    cmd, cwd = seen[0]
    assert cmd == [sys.executable, "-m", "scripts.beta_app.build", "--clean", "--no-zip"]
    assert cwd == build_module.ROOT


def test_build_returns_nonzero_without_stopping(monkeypatch):
    monkeypatch.setattr(
        build_module.subprocess,
        "run",
        lambda cmd, check=False, **kwargs: SimpleNamespace(returncode=3),
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
