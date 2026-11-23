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
        "intake": {"transport": "local", "root": "/srv/ftp1"},
        "processing": {"transport": "ftp", "host": "ftp2.example.com", "username": "watcher"},
        "jobs": ["daily-report"],
        "share_root": "/mnt/share",
        "ledger_db": "/tmp/watcher.db",
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_parses_with_defaults():
    cfg = parse_config(_base(outbound=None))
    assert cfg.intake.transport.kind == "local"
    assert cfg.intake.transport.root == "/srv/ftp1"
    assert cfg.intake.incoming_dir == "incoming"
    assert cfg.intake.done_dir == "done"
    assert cfg.intake.failed_dir == "failed"
    assert cfg.processing.transport.kind == "ftp"
    assert cfg.processing.transport.host == "ftp2.example.com"
    assert cfg.processing.transport.port == 21
    assert cfg.processing.processing_dir == "processing"
    assert cfg.processing.success_dir == "success"
    assert cfg.processing.failed_dir == "failed"
    assert cfg.jobs == ["daily-report"]
    assert cfg.outbound is None
    assert cfg.notify is None
    assert cfg.log_file is None


def test_outbound_defaults():
    cfg = parse_config(_base(outbound={}))
    assert cfg.outbound.requests_dir == "upload-requests"
    assert cfg.outbound.archive_dir == "upload-archive"
    assert cfg.outbound.min_age_seconds == 2


def test_notify_defaults():
    cfg = parse_config(
        _base(
            notify={
                "smtp_host": "smtp.example.com",
                "from": "watcher@example.com",
                "to": "ops@example.com",
            }
        )
    )
    assert cfg.notify.smtp_port == 587
    assert cfg.notify.use_tls is True
    assert cfg.notify.to == ["ops@example.com"]
    assert cfg.notify.username == ""
    assert cfg.notify.password_env is None


def test_notify_to_list():
    cfg = parse_config(
        _base(
            notify={
                "smtp_host": "smtp.example.com",
                "from": "watcher@example.com",
                "to": ["a@example.com", "b@example.com"],
            }
        )
    )
    assert cfg.notify.to == ["a@example.com", "b@example.com"]


def test_log_file_parses():
    cfg = parse_config(_base(log_file="/var/log/fileflow-watcher/watcher.log"))
    assert cfg.log_file == "/var/log/fileflow-watcher/watcher.log"


def test_example_config_parses():
    cfg = parse_config(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    assert cfg.jobs == ["monthly-report"]
    assert cfg.intake.transport.kind == "local"
    assert cfg.processing.transport.kind == "ftp"
    assert cfg.processing.transport.host == "ftp2.example.com"
    assert cfg.notify is not None
    assert cfg.notify.to == ["ops@example.com"]
    assert cfg.outbound is not None
    assert cfg.log_file == "/var/log/fileflow-watcher/watcher.log"


def test_missing_required_keys_all_reported():
    with pytest.raises(ConfigError) as exc:
        parse_config({})
    msg = str(exc.value)
    for key in ("'intake'", "'processing'", "'ledger_db'"):
        assert key in msg
    assert "'jobs' must be a non-empty list" in msg
    assert "4 problems" in msg


def test_old_source_key_rejected():
    with pytest.raises(ConfigError, match="unknown key 'source'"):
        parse_config(_base(source={"transport": "local", "root": "/srv/ftp1"}))


def test_empty_jobs_rejected():
    with pytest.raises(ConfigError, match="'jobs' must be a non-empty list"):
        parse_config(_base(jobs=[]))


def test_jobs_must_be_a_list():
    with pytest.raises(ConfigError, match="'jobs' must be a non-empty list"):
        parse_config(_base(jobs="daily-report"))


def test_unknown_top_level_key():
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(_base(typo=1))


def test_unknown_intake_key():
    cfg = _base()
    cfg["intake"]["typo"] = 1
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(cfg)


def test_unknown_processing_key():
    cfg = _base()
    cfg["processing"]["typo"] = 1
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(cfg)


def test_unknown_notify_key():
    cfg = _base(notify={"smtp_host": "h", "from": "a@b", "to": "c@d", "typo": 1})
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(cfg)


def test_bad_transport_kind():
    cfg = _base()
    cfg["intake"]["transport"] = "smoke"
    with pytest.raises(ConfigError, match="'transport' must be local\\|ftp\\|sftp"):
        parse_config(cfg)


def test_local_transport_needs_root():
    with pytest.raises(ConfigError, match="'root' is required for transport 'local'"):
        parse_config(_base(intake={"transport": "local"}))


def test_ftp_transport_needs_host():
    with pytest.raises(ConfigError, match="'host' is required for transport 'ftp'"):
        parse_config(_base(processing={"transport": "ftp"}))


def test_sftp_port_default():
    cfg = parse_config(_base(processing={"transport": "sftp", "host": "sftp.example.com"}))
    assert cfg.processing.transport.port == 22


@pytest.mark.parametrize(
    "port, problem", [(0, "'port' must be >= 1"), (70000, "'port' must be <= 65535")]
)
def test_bad_port(port, problem):
    cfg = _base()
    cfg["processing"]["port"] = port
    with pytest.raises(ConfigError, match=problem):
        parse_config(cfg)


@pytest.mark.parametrize("key, value", [("incoming_dir", "../escape"), ("done_dir", "/abs/path")])
def test_intake_dir_must_stay_under_root(key, value):
    cfg = _base()
    cfg["intake"][key] = value
    with pytest.raises(ConfigError, match="must stay under its root"):
        parse_config(cfg)


def test_processing_dir_must_stay_under_root():
    cfg = _base()
    cfg["processing"]["success_dir"] = "../escape"
    with pytest.raises(ConfigError, match="must stay under its root"):
        parse_config(cfg)


def test_job_name_must_be_plain():
    with pytest.raises(ConfigError, match="job names must be plain names"):
        parse_config(_base(jobs=["bad/name"]))


def test_notify_missing_fields():
    with pytest.raises(ConfigError) as exc:
        parse_config(_base(notify={}))
    msg = str(exc.value)
    assert "missing required key 'smtp_host'" in msg
    assert "missing required key 'from'" in msg
    assert "missing required key 'to'" in msg
    assert "3 problems" in msg


def test_notify_bad_port():
    cfg = _base(notify={"smtp_host": "h", "from": "a@b", "to": "c@d", "smtp_port": 0})
    with pytest.raises(ConfigError, match="'smtp_port' must be >= 1"):
        parse_config(cfg)


def test_share_root_required_with_outbound():
    cfg = _base(outbound={})
    del cfg["share_root"]
    with pytest.raises(ConfigError, match="'share_root' is required when 'outbound' is set"):
        parse_config(cfg)


def test_share_root_not_required_without_outbound():
    cfg = _base(outbound=None)
    del cfg["share_root"]
    assert parse_config(cfg).share_root is None


@pytest.mark.parametrize(
    "override, field",
    [
        ({"jobs": "daily-report"}, "'jobs'"),
        ({"outbound": {"min_age_seconds": -1}}, "'min_age_seconds'"),
        ({"intake": {"transport": "local", "root": "/srv/ftp1", "port": "21"}}, "'port'"),
    ],
)
def test_wrong_types_and_values(override, field):
    with pytest.raises(ConfigError, match=field):
        parse_config(_base(**override))


def test_multiple_problems_reported_at_once():
    cfg = _base(outbound={"min_age_seconds": -1})
    del cfg["ledger_db"]
    cfg["intake"]["transport"] = "smoke"
    with pytest.raises(ConfigError) as exc:
        parse_config(cfg)
    msg = str(exc.value)
    assert "3 problems" in msg
    assert "missing required key 'ledger_db'" in msg
    assert "'transport' must be local|ftp|sftp" in msg
    assert "'min_age_seconds' must be >= 0" in msg


def test_check_config_ok(tmp_path: Path):
    path = tmp_path / "watcher.json"
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 0


def test_check_config_bad(tmp_path: Path):
    path = tmp_path / "watcher.json"
    path.write_text(json.dumps(_base(typo=1)), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 2
