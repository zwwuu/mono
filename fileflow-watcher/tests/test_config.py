"""Config parser tests: one error, every problem; valid configs parse.

The shipped example config is parsed here too -- a bad example
fails the suite, not a user's first run.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_watcher.adapters.cli import main
from fileflow_watcher.infrastructure.config import ConfigError, parse_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "watcher.example.json"


def _base(**overrides):
    cfg = {
        "ftp_root": "/srv/ftp",
        "share_root": "/mnt/share",
        "ledger_db": "/tmp/watcher.db",
        "jobs": {
            "daily-report": {
                "procedure": "normalize-csv",
                "expect_csv": True,
                "share_dir": "reports/daily",
                "outbound": True,
            }
        },
        "outbound": {
            "requests_dir": "upload-requests",
            "archive_dir": "upload-archive",
            "min_age_seconds": 2,
        },
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_parses_with_defaults():
    cfg = parse_config(
        _base(
            jobs={"invoices": {"procedure": "uppercase-headers-csv"}},
            outbound=None,
        )
    )
    job = cfg.jobs["invoices"]
    assert job.procedure == "uppercase-headers-csv"
    assert job.expect_csv is False
    assert job.output_pattern == "{name}"
    assert job.share_dir == "invoices"  # default: the job name
    assert job.outbound is False
    assert cfg.outbound is None
    assert cfg.procedures_module is None


def test_outbound_defaults():
    cfg = parse_config(_base(outbound={}))
    assert cfg.outbound.requests_dir == "upload-requests"
    assert cfg.outbound.archive_dir == "upload-archive"
    assert cfg.outbound.min_age_seconds == 2


def test_example_config_parses():
    cfg = parse_config(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    assert sorted(cfg.jobs) == ["daily-report", "invoices"]
    assert cfg.jobs["daily-report"].procedure == "normalize-csv"
    assert cfg.jobs["invoices"].output_pattern == "PROC-{name}"
    assert cfg.outbound is not None


def test_missing_required_keys_all_reported():
    with pytest.raises(ConfigError) as exc:
        parse_config({})
    msg = str(exc.value)
    for key in ("'ftp_root'", "'share_root'", "'ledger_db'"):
        assert key in msg
    assert "'jobs' must be a non-empty object" in msg
    assert "4 problems" in msg


def test_empty_jobs_rejected():
    with pytest.raises(ConfigError, match="'jobs' must be a non-empty object"):
        parse_config(_base(jobs={}))


def test_unknown_top_level_key():
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(_base(typo=1))


def test_unknown_job_key():
    cfg = _base()
    cfg["jobs"]["daily-report"]["typo"] = 1
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(cfg)


def test_unknown_procedure():
    cfg = _base()
    cfg["jobs"]["daily-report"]["procedure"] = "nope"
    with pytest.raises(ConfigError, match="unknown procedure 'nope'"):
        parse_config(cfg)


def test_missing_procedure():
    cfg = _base()
    del cfg["jobs"]["daily-report"]["procedure"]
    with pytest.raises(ConfigError, match="missing required key 'procedure'"):
        parse_config(cfg)


def test_output_pattern_unknown_token():
    cfg = _base()
    cfg["jobs"]["daily-report"]["output_pattern"] = "{name}-{checksum}{ext}"
    with pytest.raises(ConfigError, match="unknown token 'checksum'"):
        parse_config(cfg)


def test_output_pattern_must_be_one_filename():
    cfg = _base()
    cfg["jobs"]["daily-report"]["output_pattern"] = "{name}/x.csv"
    with pytest.raises(ConfigError, match="must produce one filename"):
        parse_config(cfg)


@pytest.mark.parametrize("share_dir", ["../escape", "/abs/path"])
def test_share_dir_must_stay_under_share_root(share_dir):
    cfg = _base()
    cfg["jobs"]["daily-report"]["share_dir"] = share_dir
    with pytest.raises(ConfigError, match="must stay under its root"):
        parse_config(cfg)


def test_job_name_must_be_plain():
    cfg = _base(jobs={"bad/name": {"procedure": "normalize-csv"}})
    with pytest.raises(ConfigError, match="job names must be plain names"):
        parse_config(cfg)


@pytest.mark.parametrize(
    "override, field",
    [
        (
            {"jobs": {"daily-report": {"procedure": "normalize-csv", "expect_csv": "yes"}}},
            "'expect_csv'",
        ),
        ({"outbound": {"min_age_seconds": -1}}, "'min_age_seconds'"),
        ({"jobs": []}, "'jobs'"),
    ],
)
def test_wrong_types_and_values(override, field):
    with pytest.raises(ConfigError, match=field):
        parse_config(_base(**override))


def test_multiple_problems_reported_at_once():
    cfg = _base(outbound={"min_age_seconds": -1})
    del cfg["ledger_db"]
    cfg["jobs"]["daily-report"]["procedure"] = "nope"
    with pytest.raises(ConfigError) as exc:
        parse_config(cfg)
    msg = str(exc.value)
    assert "3 problems" in msg
    assert "missing required key 'ledger_db'" in msg
    assert "unknown procedure 'nope'" in msg
    assert "'min_age_seconds' must be >= 0" in msg


def test_check_config_ok(tmp_path: Path):
    path = tmp_path / "watcher.json"
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 0


def test_check_config_bad(tmp_path: Path):
    path = tmp_path / "watcher.json"
    path.write_text(json.dumps(_base(typo=1)), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 2


def _job_with(**job_overrides):
    cfg = _base()
    cfg["jobs"]["daily-report"].update(job_overrides)
    return cfg


def test_job_variables_are_the_output_tokens():
    # The token set is the job's business logic: an output pattern
    # using the job's own variable and its own date format parses.
    cfg = parse_config(
        _job_with(
            variables={"client": "acme"},
            date_format="%Y-%m-%d",
            output_pattern="{date}_{client}_{name}",
        )
    )
    job = cfg.jobs["daily-report"]
    assert job.variables == {"client": "acme"}
    assert job.date_format == "%Y-%m-%d"
    assert job.output_pattern == "{date}_{client}_{name}"


@pytest.mark.parametrize(
    "variables, problem",
    [
        ({"name": "x"}, "collides with built-in token 'name'"),
        ({"date": "x"}, "collides with built-in token 'date'"),
        ({"my-var": "x"}, "variable names must be identifiers"),
        ({"client": 3}, "variable 'client' must be a string"),
    ],
)
def test_job_variables_rejected(variables, problem):
    with pytest.raises(ConfigError, match=problem):
        parse_config(_job_with(variables=variables))


def test_job_date_format_must_not_be_empty():
    with pytest.raises(ConfigError, match="'date_format' must be a non-empty string"):
        parse_config(_job_with(date_format=""))


def test_output_pattern_unknown_token_still_fails():
    cfg = _job_with(variables={"client": "acme"}, output_pattern="{client}_{nope}_{name}")
    with pytest.raises(ConfigError, match="unknown token 'nope'"):
        parse_config(cfg)
