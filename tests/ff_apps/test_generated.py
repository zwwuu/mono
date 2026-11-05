"""Tests for the ff-* scenario apps.

Each scenario from FileFlow's scenario library was extracted into its
own app as src/apps/ff-<id>/scenario.json. The app resolves the
pipeline at runtime with FileFlow's build_pipeline() and executes it
headlessly via apps.fileflow.core.headless.
"""

import importlib
import json
from pathlib import Path

import pytest

from apps.fileflow.core.tasks import validate_task
from common.pipeline import STEP_FUNCS, TaskError, build_pipeline, run_pipeline

ROOT = Path(__file__).resolve().parents[2]
APPS_DIR = ROOT / "src" / "apps"

FF_PKGS = sorted(p.name for p in APPS_DIR.iterdir() if p.name.startswith("ff_"))


def _scenario_id(pkg):
    return pkg[3:].replace("_", "-")


def _load_scenario(pkg):
    return json.loads((APPS_DIR / pkg / "scenario.json").read_text(encoding="utf-8"))


def test_sixteen_apps_extracted():
    assert len(FF_PKGS) == 16, FF_PKGS


def test_scenario_ids_unique():
    ids = [_load_scenario(pkg)["id"] for pkg in FF_PKGS]
    assert len(ids) == len(set(ids)), ids


@pytest.mark.parametrize("pkg", FF_PKGS)
def test_scenario_valid_and_builds(pkg):
    """Each app's scenario.json passes validation and builds a pipeline
    that starts with fetch (browser sources need playwright to validate)."""
    scenario = _load_scenario(pkg)
    assert scenario["id"] == _scenario_id(pkg)
    try:
        validate_task(scenario)
    except ModuleNotFoundError as exc:
        pytest.skip(f"optional dependency missing: {exc}")
    except TaskError as exc:
        pytest.fail(f"scenario {scenario['id']} invalid: {exc}")
    pipeline = build_pipeline(scenario)
    assert pipeline, "empty pipeline"
    assert pipeline[0][2] == "fetch"


@pytest.mark.parametrize("pkg", FF_PKGS)
def test_app_imports_and_help(pkg):
    """Package imports; the AppConfig subclass declares identity; --help works."""
    main_mod = importlib.import_module(f"apps.{pkg}.main")
    app_cls = next(
        v
        for v in vars(main_mod).values()
        if isinstance(v, type) and v.__module__ == f"apps.{pkg}.main" and v.__name__.endswith("App")
    )
    assert app_cls.APP_NAME == f"ff-{_scenario_id(pkg)}"
    assert app_cls.VERSION
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize("pkg", FF_PKGS)
def test_step_types_known(pkg):
    scenario = _load_scenario(pkg)
    try:
        pipeline = build_pipeline(scenario)
    except ModuleNotFoundError:
        pytest.skip("optional dependency missing")
    for _key, _label, stype, _params in pipeline:
        assert stype in STEP_FUNCS, stype


def _write_source(tmp_path):
    src = tmp_path / "report.csv"
    src.write_text("a,b\n", encoding="utf-8")
    return src


def test_headless_e2e_no_destination(tmp_path):
    """fetch -> checksum -> rename headlessly on a real file."""
    from apps.ff_scenario_no_destination.main import main

    src = _write_source(tmp_path)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"source": {"type": "local", "path": str(src)}}))
    code = main(["--config", str(cfg_file), "--workdir", str(tmp_path / "work")])
    assert code == 0
    renamed = list((tmp_path / "work").rglob("*_report.csv"))
    assert len(renamed) == 1


def test_headless_skips_approval(tmp_path):
    """Approval steps are skipped headless; the rest of the pipeline runs."""
    from apps.ff_scenario_multi_approval.main import main

    src = _write_source(tmp_path)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source": {"type": "local", "path": str(src)},
                "recipients": ["ops@example.com"],
            }
        )
    )
    code = main(["--config", str(cfg_file), "--workdir", str(tmp_path / "work")])
    assert code == 0
    # notify fell back to the outbox (no SMTP configured)
    outbox = list((tmp_path / "work").rglob("*.eml"))
    assert len(outbox) == 1


def test_headless_missing_config_returns_2(tmp_path):
    """A missing --config file is exit 2 (config error, before the pipeline)."""
    from apps.ff_scenario_no_destination.main import main

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(tmp_path / "missing.json"),
                "--workdir",
                str(tmp_path / "work"),
            ]
        )
    assert exc.value.code == 2


def test_headless_step_failure_returns_1(tmp_path):
    """A failing fetch (bad source) is exit 1, not a traceback."""
    from apps.ff_scenario_no_destination.main import main

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"source": {"type": "local", "path": str(tmp_path / "nope.csv")}})
    )
    code = main(["--config", str(cfg_file), "--workdir", str(tmp_path / "work")])
    assert code == 1


class RecLog:
    """Recording stand-in for a Logger (mono logger doesn't propagate)."""

    def __init__(self):
        self.records = []

    def _rec(self, level, msg, *args):
        self.records.append((level, msg % args if args else msg))

    def debug(self, msg, *args):
        self._rec("debug", msg, *args)

    def info(self, msg, *args):
        self._rec("info", msg, *args)

    def warning(self, msg, *args):
        self._rec("warning", msg, *args)

    def error(self, msg, *args):
        self._rec("error", msg, *args)

    def exception(self, msg, *args):
        self._rec("exception", msg, *args)


def test_headless_run_limit_warns(tmp_path):
    """run_limit in the task warns instead of being enforced."""
    scenario = _load_scenario("ff_scenario_run_limit")
    assert scenario.get("run_limit")
    task = {
        "id": scenario["id"],
        "name": scenario.get("name", scenario["id"]),
        "source": scenario.get("source", {}),
        "run_limit": scenario["run_limit"],
    }
    log = RecLog()
    run_pipeline(build_pipeline(scenario), task, {}, log, tmp_path / "work")
    assert any(
        level == "warning" and "run limits are not enforced" in msg for level, msg in log.records
    ), log.records
