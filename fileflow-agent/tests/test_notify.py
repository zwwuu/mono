"""Notification emails and file logging tests.

SMTP is faked: the notifier takes an smtp_factory, so no test
ever opens a socket. The file-logging tests call setup_logging
directly and remove the file handler they add, so no test leaks a
handler into the next one.
"""

import hashlib
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_contract import file_entry, write_manifest

from fileflow_agent.adapters.cli import build_download_use_case, build_pickup_use_case
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
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
    }
    if notify is not None:
        cfg["notify"] = notify
    return cfg


def _run_once(cfg: dict, notifier) -> dict:
    use_case, transport, repository = build_download_use_case(parse_config(cfg), notifier=notifier)
    try:
        return use_case.run()
    finally:
        transport.close()
        repository.close()


def _pickup_once(cfg: dict, notifier) -> dict[str, int]:
    use_case, transport, repository = build_pickup_use_case(parse_config(cfg), notifier=notifier)
    try:
        return use_case.run()
    finally:
        transport.close()
        repository.close()


def test_notify_sent_on_uploaded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FILEFLOW_SMTP_PASSWORD", "secret")
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify,
        smtp_factory=_factory(instances),
    )
    result = _run_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
    assert result["status"] == "UPLOADED"
    assert len(instances) == 1
    smtp = instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.tls is True  # STARTTLS on 587
    assert smtp.login_args == ("agent@example.com", "secret")
    assert len(smtp.messages) == 1
    msg = smtp.messages[0]
    assert "UPLOADED" in msg["Subject"]
    assert "daily-report" in msg["Subject"]
    assert msg["From"] == "agent@example.com"
    assert msg["To"] == "ops@example.com"
    body = msg.get_content()
    assert result["outputs"][0] in body
    assert "incoming/daily-report" in body


def test_notify_sent_on_failed(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text("", encoding="utf-8")  # empty: validate_input fails
    instances: list[FakeSMTP] = []
    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify,
        smtp_factory=_factory(instances),
    )
    with pytest.raises(ValueError, match="empty"):
        _run_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
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
    assert _run_once(cfg, notifier)["status"] == "UPLOADED"
    assert _run_once(cfg, notifier)["status"] == "SKIPPED"
    # One email for the upload; the SKIPPED re-run sends nothing.
    assert sum(len(s.messages) for s in instances) == 1


def test_no_notify_section_sends_nothing_and_does_not_crash(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")

    def exploding_factory(host: str, port: int):
        raise AssertionError("SMTP must not be touched without a notify section")

    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source)).notify, smtp_factory=exploding_factory
    )
    result = _run_once(_agent_cfg(tmp_path, source), notifier)
    assert result["status"] == "UPLOADED"


def test_notify_failure_does_not_fail_the_run(tmp_path: Path):
    # A dead mail server must not turn a successful run into a failure.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")

    def dead_factory(host: str, port: int):
        raise OSError("connection refused")

    notifier = SmtpNotifier(
        parse_config(_agent_cfg(tmp_path, source, notify=_NOTIFY)).notify, smtp_factory=dead_factory
    )
    result = _run_once(_agent_cfg(tmp_path, source, notify=_NOTIFY), notifier)
    assert result["status"] == "UPLOADED"


class UploadHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # http.server naming
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        start = body.find(b'filename="')
        header_end = body.find(b"\r\n\r\n", start)
        content = body[header_end + 4 : body.rfind(b"\r\n--")]
        payload = json.dumps(
            {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _serve() -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", 0), UploadHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _seed_outgoing(tmp_path: Path, content: str = "id,amount\n7,99\n") -> Path:
    """Stage a file in outgoing/ exactly as the watcher would."""
    out_dir = tmp_path / "ftp" / "outgoing" / "daily-report"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = out_dir / "to-upload.csv"
    data.write_text(content, encoding="utf-8")
    manifest = {
        "version": 1,
        "run_id": "0123456789abcdef",
        "job": "daily-report",
        "direction": "outbound",
        "idempotency_key": "daily-report|0123456789abcdef",
        "files": [file_entry(data, "output")],
    }
    write_manifest(out_dir / "to-upload.manifest.json", manifest)
    return out_dir


def _pickup_cfg(tmp_path: Path, port: int) -> dict:
    return {
        "job": "daily-report",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
        "deliver": {
            "upload_url": f"http://127.0.0.1:{port}/upload",
            "form_field": "file",
        },
    }


def test_pickup_email_on_uploaded(tmp_path: Path):
    server, port = _serve()
    try:
        _seed_outgoing(tmp_path)
        cfg = _pickup_cfg(tmp_path, port)
        cfg["notify"] = _NOTIFY
        instances: list[FakeSMTP] = []
        notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
        assert _pickup_once(cfg, notifier) == {"UPLOADED_TO_WEBSITE": 1}
        assert len(instances) == 1
        msg = instances[0].messages[0]
        assert "UPLOADED_TO_WEBSITE" in msg["Subject"]
        assert "to-upload.csv" in msg.get_content()
    finally:
        server.shutdown()


def test_pickup_email_on_failed(tmp_path: Path):
    server, port = _serve()
    try:
        out_dir = _seed_outgoing(tmp_path)
        staged = out_dir / "to-upload.csv"
        staged.write_bytes(staged.read_bytes() + b"X")  # corrupt the hash
        cfg = _pickup_cfg(tmp_path, port)
        cfg["notify"] = _NOTIFY
        instances: list[FakeSMTP] = []
        notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
        assert _pickup_once(cfg, notifier) == {"FAILED": 1}
        assert len(instances) == 1
        msg = instances[0].messages[0]
        assert "FAILED" in msg["Subject"]
        assert "verification failed" in msg.get_content()
    finally:
        server.shutdown()


def test_pickup_duplicate_sends_no_email(tmp_path: Path):
    # A pass that only clears an already-uploaded batch is cleanup,
    # not a delivery -- like SKIPPED, it sends nothing. The duplicate
    # branch needs the batch re-staged after the first upload: the
    # run is already recorded UPLOADED_TO_WEBSITE, but the outgoing
    # files are back (a crash between upload and cleanup).
    server, port = _serve()
    try:
        _seed_outgoing(tmp_path)
        cfg = _pickup_cfg(tmp_path, port)
        cfg["notify"] = _NOTIFY
        instances: list[FakeSMTP] = []
        notifier = SmtpNotifier(parse_config(cfg).notify, smtp_factory=_factory(instances))
        assert _pickup_once(cfg, notifier) == {"UPLOADED_TO_WEBSITE": 1}
        _seed_outgoing(tmp_path)  # same run_id, staged again
        assert _pickup_once(cfg, notifier) == {"DUPLICATE": 1}
        assert sum(len(s.messages) for s in instances) == 1
    finally:
        server.shutdown()


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
