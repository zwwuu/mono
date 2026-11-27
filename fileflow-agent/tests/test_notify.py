"""Notification emails and file logging tests.

SMTP is faked: the notifier takes an smtp_factory, so no test
ever opens a socket. The file-logging tests call setup_logging
directly and remove the file handler they add, so no test leaks a
handler into the next one.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_agent.adapters.cli import build_pull_use_case, build_push_use_case
from fileflow_agent.infrastructure.config import parse_config
from fileflow_agent.infrastructure.filesystem import log, setup_logging
from fileflow_agent.infrastructure.notify import SmtpNotifier


class FakeSMTP:
    """Records what the notifier sends; never opens a socket."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.tls = False
        self.login_args: tuple | None = None
        self.messages: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user: str, password: str):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.messages.append(msg)

    def quit(self):
        pass


def _factory(instances: list):
    def make(host: str, port: int):
        smtp = FakeSMTP(host, port)
        instances.append(smtp)
        return smtp

    return make


_NOTIFY = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "from": "agent@example.com",
    "to": "ops@example.com",
    "password_env": "FILEFLOW_SMTP_PASSWORD",
}


def _agent_cfg(tmp_path: Path, source: Path, *, notify: dict | None = None) -> dict:
    cfg = {
        "job": "daily-report",
        "source": {"type": "local", "path": str(source)},
        "expect_csv": True,
        "procedure": "normalize-csv",
        "rename_pattern": "{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "db_url": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
    }
    if notify is not None:
        cfg["notify"] = notify
    return cfg


def _pull_once(cfg: dict, notifier) -> dict:
    use_case, transport, repository = build_pull_use_case(parse_config(cfg), notifier=notifier)
    try:
        return use_case.run()
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def _push_once(cfg: dict, notifier) -> dict:
    use_case, transport, repository = build_push_use_case(parse_config(cfg), notifier=notifier)
    try:
        return use_case.run()
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def test_notify_sent_on_staged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FILEFLOW_SMTP_PASSWORD", "secret")
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify,
        smtp_factory=_factory(instances),
    )
    result = _pull_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
    assert result["status"] == "STAGED"
    assert len(instances) == 1
    smtp = instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.tls is True  # STARTTLS on 587
    assert smtp.login_args == ("agent@example.com", "secret")
    assert len(smtp.messages) == 1
    msg = smtp.messages[0]
    assert "STAGED" in msg["Subject"]
    assert "daily-report" in msg["Subject"]
    assert msg["From"] == "agent@example.com"
    assert msg["To"] == "ops@example.com"
    body = msg.get_content()
    assert result["outputs"][0] in body
    assert "staging" in body


def test_notify_sent_on_failed(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text("", encoding="utf-8")  # empty: validate_input fails
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify,
        smtp_factory=_factory(instances),
    )
    with pytest.raises(ValueError, match="empty"):
        _pull_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
    assert len(instances) == 1
    msg = instances[0].messages[0]
    assert "FAILED" in msg["Subject"]
    assert "daily.csv" in msg.get_content()  # the error names the file


def test_no_email_on_skipped(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    instances: list[FakeSMTP] = []
    cfg = _agent_cfg(tmp_path, source, notify=_NOTIFY)
    notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
    assert _pull_once(cfg, notifier)["status"] == "STAGED"
    assert _pull_once(cfg, notifier)["status"] == "SKIPPED"
    # One email for the pull; the SKIPPED re-run sends nothing.
    assert sum(len(s.messages) for s in instances) == 1


def test_no_notify_section_sends_nothing_and_does_not_crash(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")

    def exploding_factory(host: str, port: int):
        raise AssertionError("SMTP must not be touched without a notify section")

    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source)).notify, smtp_factory=exploding_factory
    )
    result = _pull_once(_agent_cfg(tmp_path, source), notifier)
    assert result["status"] == "STAGED"


def test_notify_failure_does_not_fail_the_run(tmp_path: Path):
    # A dead mail server must not turn a successful run into a failure.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")

    def dead_factory(host: str, port: int):
        raise OSError("connection refused")

    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify, smtp_factory=dead_factory
    )
    result = _pull_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
    assert result["status"] == "STAGED"


def test_push_summary_email_on_uploaded(tmp_path: Path):
    # One email per push run, summarising every batch it handled.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source, notify=_NOTIFY)
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
    assert _pull_once(cfg, notifier)["status"] == "STAGED"
    assert _push_once(cfg, notifier)["status"] == "UPLOADED"
    assert len(instances) == 2  # one for the pull, one for the push
    msg = instances[1].messages[0]
    assert "UPLOADED" in msg["Subject"]
    body = msg.get_content()
    assert "daily.csv" in body
    assert "incoming/daily-report" in body


def test_push_email_on_failed(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source, notify=_NOTIFY)
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
    pulled = _pull_once(cfg, notifier)
    staged = tmp_path / "staging" / pulled["run_id"] / "daily.csv"
    staged.write_bytes(staged.read_bytes() + b"X")  # corrupt the hash
    with pytest.raises(RuntimeError, match=pulled["run_id"]):
        _push_once(cfg, notifier)
    assert len(instances) == 2  # pull STAGED email + push FAILED email
    msg = instances[1].messages[0]
    assert "FAILED" in msg["Subject"]
    assert pulled["run_id"] in msg.get_content()
    assert "verification failed" in msg.get_content()


def test_no_email_when_push_has_nothing_staged(tmp_path: Path):
    cfg = _agent_cfg(tmp_path, tmp_path / "daily.csv", notify=_NOTIFY)
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
    assert _push_once(cfg, notifier) == {"status": "NOOP", "uploaded": [], "files": 0}
    assert sum(len(s.messages) for s in instances) == 0


def _remove_file_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, TimedRotatingFileHandler):
            root.removeHandler(handler)
            handler.close()


def test_log_file_receives_agent_logs(tmp_path: Path):
    log_path = tmp_path / "agent.log"
    try:
        setup_logging("INFO", log_file=log_path)
        log.info("hello %s", "log-file")
        handler = next(
            h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)
        )
        assert handler.backupCount == 7
        assert log_path.exists()
        assert "hello log-file" in log_path.read_text(encoding="utf-8")
    finally:
        _remove_file_handlers()


def test_no_log_file_creates_no_file(tmp_path: Path):
    setup_logging("INFO")
    log.info("nothing to write anywhere")
    assert list(tmp_path.glob("*.log")) == []
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in logging.getLogger().handlers)


def test_setup_logging_same_file_twice_adds_one_handler(tmp_path: Path):
    log_path = tmp_path / "agent.log"
    try:
        setup_logging("INFO", log_file=log_path)
        setup_logging("INFO", log_file=log_path)
        count = sum(
            1 for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)
        )
        assert count == 1
    finally:
        _remove_file_handlers()


def test_setup_logging_different_file_switches_handler(tmp_path: Path):
    # A date template rolling over: the old file stops receiving
    # records, the new one gets them, and there is still one handler.
    first = tmp_path / "agent-20200101.log"
    second = tmp_path / "agent-20200102.log"
    try:
        setup_logging("INFO", log_file=first)
        log.info("first file")
        setup_logging("INFO", log_file=second)
        log.info("second file")
        handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)
        ]
        assert len(handlers) == 1
        assert Path(handlers[0].baseFilename) == second
        assert "first file" in first.read_text(encoding="utf-8")
        assert "second file" not in first.read_text(encoding="utf-8")
        assert "second file" in second.read_text(encoding="utf-8")
    finally:
        _remove_file_handlers()
