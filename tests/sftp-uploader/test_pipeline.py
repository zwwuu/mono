"""End-to-end tests against the stub SFTP server (real SFTP protocol,
localhost only — no production server needed)."""
import json
from unittest import mock

import paramiko

from sftp_uploader.config import load_config
from sftp_uploader.log import setup_logging
from sftp_uploader.pipeline import Pipeline


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def make_config(tmp_path, server, **overrides):
    (tmp_path / "outbox").mkdir(exist_ok=True)
    cfg = {
        "sftp": {
            "host": "127.0.0.1",
            "port": server.port,
            "username": "tester",
            "password": "secret",
            "remote_dir": "/data/incoming",
            "create_remote_dir": True,
            "host_key_policy": "auto_add",
            "timeout": 10,
        },
        "file": {
            "local_dir": str(tmp_path / "outbox"),
            "pattern": "report-*.csv",
            "pick": "newest",
        },
        "retry": {"max_attempts": 3, "backoff_seconds": 0, "backoff_max_seconds": 0},
        "email": {"enabled": True, "smtp_host": "x", "from": "a@x", "to": ["b@x"]},
        "logging": {"level": "CRITICAL"},
        "verify": True,
    }
    for section, vals in overrides.items():
        cfg[section].update(vals)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return load_config(str(path))


def run(cfg):
    FakeSMTP.sent.clear()
    with mock.patch("smtplib.SMTP", FakeSMTP):
        setup_logging(cfg)
        return Pipeline(cfg).execute()


def test_upload_success(sftp_server, tmp_path):
    cfg = make_config(tmp_path, sftp_server)
    (tmp_path / "outbox" / "report-20240101.csv").write_bytes(b"x" * 5000)

    assert run(cfg) == 0

    stored = tmp_path / "srv" / "data" / "incoming" / "report-20240101.csv"
    assert stored.read_bytes() == b"x" * 5000  # nested dirs created, bytes intact
    assert len(FakeSMTP.sent) == 1
    assert "OK" in FakeSMTP.sent[0]["Subject"]
    assert "report-20240101.csv" in FakeSMTP.sent[0]["Subject"]


def test_retry_then_success(sftp_server, tmp_path):
    cfg = make_config(tmp_path, sftp_server)
    (tmp_path / "outbox" / "report-a.csv").write_bytes(b"data")
    sftp_server.state["fail_puts"] = 2

    assert run(cfg) == 0
    assert "attempt 3" in FakeSMTP.sent[0].get_content()


def test_failure_names_step(sftp_server, tmp_path):
    cfg = make_config(
        tmp_path, sftp_server,
        retry={"max_attempts": 2, "backoff_seconds": 0, "backoff_max_seconds": 0},
    )
    (tmp_path / "outbox" / "report-a.csv").write_bytes(b"data")
    sftp_server.state["fail_puts"] = 99

    assert run(cfg) == 1
    subject = FakeSMTP.sent[0]["Subject"]
    assert "FAILED" in subject and "upload" in subject
    assert "step 'upload'" in FakeSMTP.sent[0].get_content()


def test_verbose_flag(sftp_server, tmp_path):
    import logging

    from sftp_uploader import __main__ as main_mod

    cfg = make_config(tmp_path, sftp_server)
    (tmp_path / "outbox" / "report-v.csv").write_bytes(b"v")
    code = main_mod.main(
        ["--config", str(tmp_path / "config.json"), "--dry-run", "--verbose"]
    )
    assert code == 0
    assert logging.getLogger("sftp_uploader").level == logging.DEBUG
    setup_logging(cfg)  # restore CRITICAL for subsequent tests


def test_no_file_match(sftp_server, tmp_path):
    cfg = make_config(tmp_path, sftp_server)
    assert run(cfg) == 3


def test_key_auth(sftp_server, tmp_path):
    key = paramiko.RSAKey.generate(1024)
    key_path = tmp_path / "id_test"
    key.write_private_key_file(str(key_path))
    sftp_server.pubkey_blob = key.asbytes()
    sftp_server.password = None

    cfg = make_config(
        tmp_path, sftp_server, sftp={"key_file": str(key_path), "password": None}
    )
    (tmp_path / "outbox" / "report-k.csv").write_bytes(b"k")

    assert run(cfg) == 0
    stored = tmp_path / "srv" / "data" / "incoming" / "report-k.csv"
    assert stored.read_bytes() == b"k"


def _seed_remote(tmp_path, name, content):
    d = tmp_path / "srv" / "data" / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(content)
    return d / name


def test_existing_file_overwrite(sftp_server, tmp_path):
    cfg = make_config(tmp_path, sftp_server)  # default on_existing=overwrite
    _seed_remote(tmp_path, "report-a.csv", b"old")
    (tmp_path / "outbox" / "report-a.csv").write_bytes(b"new")

    assert run(cfg) == 0
    stored = tmp_path / "srv" / "data" / "incoming" / "report-a.csv"
    assert stored.read_bytes() == b"new"
    assert "OK" in FakeSMTP.sent[0]["Subject"]


def test_existing_file_skip(sftp_server, tmp_path):
    cfg = make_config(tmp_path, sftp_server, sftp={"on_existing": "skip"})
    _seed_remote(tmp_path, "report-a.csv", b"same")
    (tmp_path / "outbox" / "report-a.csv").write_bytes(b"same")

    assert run(cfg) == 0
    stored = tmp_path / "srv" / "data" / "incoming" / "report-a.csv"
    assert stored.read_bytes() == b"same"  # untouched
    assert "SKIPPED" in FakeSMTP.sent[0]["Subject"]


def test_existing_file_fail(sftp_server, tmp_path):
    cfg = make_config(
        tmp_path,
        sftp_server,
        sftp={"on_existing": "fail"},
        retry={"max_attempts": 1, "backoff_seconds": 0, "backoff_max_seconds": 0},
    )
    _seed_remote(tmp_path, "report-a.csv", b"old")
    (tmp_path / "outbox" / "report-a.csv").write_bytes(b"new")

    assert run(cfg) == 1
    subject = FakeSMTP.sent[0]["Subject"]
    assert "FAILED" in subject and "upload" in subject
