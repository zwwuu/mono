"""Tests for FileFlow v0.5.0 sources: ftp, smb, local-dir, browser."""
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from apps.fileflow.core.tasks import TaskError, validate_task
from apps.fileflow.infra import transfers
from apps.fileflow.infra.browser_fetch import (
    BrowserSourceError,
    validate_actions,
)
from apps.fileflow.infra.demo import ensure_demo_source
from tests.flow.helpers import make_engine, seed, wait_status


def test_pick_entry():
    entries = [("a.csv", 10), ("b.csv", 20), ("c.txt", 30)]
    assert transfers.pick_entry(entries, "*.csv", "newest") == "b.csv"
    assert transfers.pick_entry(entries, "*.csv", "oldest") == "a.csv"
    assert transfers.pick_entry(entries, "*", "name") == "a.csv"
    assert transfers.pick_entry([("x", None), ("y", None)], "*", "newest") == "x"
    with pytest.raises(transfers.TransferError):
        transfers.pick_entry(entries, "*.json", "newest")
    with pytest.raises(transfers.TransferError):
        transfers.pick_entry(entries, "*", "sideways")


def test_fetch_from_local_dir():
    task = {
        "id": "ld",
        "name": "LocalDir",
        "source": {"type": "local", "dir": "inbox", "pattern": "report-*.csv", "sort": "newest"},
        "approval": False,
        "notify": False,
        "steps": [
            {"type": "fetch"},
            {"type": "checksum"},
            {"type": "rename", "pattern": "{name}{ext}"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        seed(cfg.path("inbox", "report-old.csv"), "old")
        seed(cfg.path("inbox", "report-new.csv"), "new")
        seed(cfg.path("inbox", "notes.txt"), "notes")
        os.utime(cfg.path("inbox", "report-old.csv"), (1000, 1000))
        os.utime(cfg.path("inbox", "report-new.csv"), (2000, 2000))
        run_id = engine.create_and_start("ld")
        run = wait_status(db, run_id, "COMPLETED")
        assert run["context"]["downloaded_name"] == "report-new.csv"


def test_fetch_from_ftp_demo():
    task = {
        "id": "ft",
        "name": "Ftp",
        "source": {"type": "ftp", "dir": "inbox", "pattern": "*.csv", "sort": "oldest"},
        "approval": False,
        "notify": False,
        "steps": [{"type": "fetch"}, {"type": "checksum"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        seed(cfg.path("demo_ftp", "inbox", "first.csv"), "first")
        seed(cfg.path("demo_ftp", "inbox", "second.csv"), "second")
        os.utime(cfg.path("demo_ftp", "inbox", "first.csv"), (1000, 1000))
        os.utime(cfg.path("demo_ftp", "inbox", "second.csv"), (2000, 2000))
        run_id = engine.create_and_start("ft")
        run = wait_status(db, run_id, "COMPLETED")
        assert run["context"]["downloaded_name"] == "first.csv"
        assert run["context"]["source_url"] == "ftp://inbox/first.csv"


def test_fetch_from_smb_demo():
    task = {
        "id": "sm",
        "name": "Smb",
        "source": {"type": "smb", "dir": "share", "pattern": "data-*.csv"},
        "approval": False,
        "notify": False,
        "steps": [{"type": "fetch"}, {"type": "checksum"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        seed(cfg.path("demo_smb", "share", "data-20261024.csv"), "smb-data")
        run_id = engine.create_and_start("sm")
        run = wait_status(db, run_id, "COMPLETED")
        assert run["context"]["downloaded_name"] == "data-20261024.csv"


def test_browser_validation():
    good = [
        {"action": "goto", "url": "https://portal.example.com/login"},
        {"action": "fill", "selector": "#user", "value": "alice"},
        {"action": "fill", "selector": "#pass", "secret": "PORTAL_PASSWORD"},
        {"action": "click", "selector": "button[type=submit]"},
        {"action": "wait_for", "selector": "a.download"},
        {"action": "download", "selector": "a.download"},
    ]
    assert validate_actions(good) == good
    for bad in (
        [],
        [{"action": "goto", "url": "https://x"}],
        [{"action": "teleport", "selector": "x"}, {"action": "download", "selector": "x"}],
        [
            {"action": "fill", "selector": "#u", "value": "a", "secret": "X"},
            {"action": "download", "selector": "x"},
        ],
        [{"action": "fill", "selector": "#u"}, {"action": "download", "selector": "x"}],
        [{"action": "download"}],
    ):
        with pytest.raises(BrowserSourceError):
            validate_actions(bad)
    with pytest.raises(TaskError):
        validate_task({"id": "bad", "name": "Bad", "source": {"type": "browser", "actions": []}})


def test_browser_dispatch_mocked(monkeypatch):
    import apps.fileflow.infra.browser_fetch as browser_mod

    def fake_run(source, dest_dir, log):
        path = os.path.join(dest_dir, "portal-report.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("portal-data")
        return path, "portal-report.csv", "https://portal.example.com/files"

    monkeypatch.setattr(browser_mod, "run_browser_source", fake_run)
    task = {
        "id": "br",
        "name": "Browser",
        "source": {
            "type": "browser",
            "actions": [
                {"action": "goto", "url": "https://portal.example.com/login"},
                {"action": "fill", "selector": "#user", "value": "alice"},
                {"action": "click", "selector": "button"},
                {"action": "download", "selector": "a.download"},
            ],
        },
        "approval": False,
        "notify": False,
        "steps": [{"type": "fetch"}, {"type": "checksum"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        run_id = engine.create_and_start("br")
        run = wait_status(db, run_id, "COMPLETED")
        assert run["context"]["downloaded_name"] == "portal-report.csv"
        assert run["context"]["source_url"] == "https://portal.example.com/files"


def test_browser_missing_chromium(monkeypatch):
    """With no Chromium binary available, a browser source fails with a
    clear 'playwright install chromium' message, not a raw launch error."""
    import apps.fileflow.infra.browser_fetch as browser_mod

    class _NoBinary:
        class chromium:
            @staticmethod
            def launch(headless=True):
                raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _NoBinaryCtx(_NoBinary))

    class _NoBinaryCtx:
        def __init__(self, target):
            self.target = target

        def __enter__(self):
            return self.target

        def __exit__(self, *exc):
            return False

    with pytest.raises(BrowserSourceError, match="playwright install chromium"):
        browser_mod.run_browser_source(
            {"actions": [{"action": "download", "selector": "x"}]}, "/tmp", lambda m: None
        )


# ------------------------------------------------------------ real browser
class _PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/login":
            body = (
                '<form method="post" action="/login">'
                '<input id="user" name="user">'
                '<input id="pass" name="pass" type="password">'
                '<button type="submit">Sign in</button></form>'
            )
            self._send(200, body, "text/html")
        elif self.path == "/files":
            body = '<a class="download" href="/report.csv">Download</a>'
            self._send(200, body, "text/html")
        elif self.path == "/report.csv":
            self._send(200, "id,amount\n1,100\n", "text/csv", attachment="portal-report.csv")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode())
        if (
            self.path == "/login"
            and fields.get("user") == ["alice"]
            and fields.get("pass") == ["secret"]
        ):
            self.send_response(302)
            self.send_header("Location", "/files")
            self.end_headers()
        else:
            self._send(401, "bad login", "text/plain")

    def _send(self, code, body, ctype, attachment=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)


def test_browser_real_login_download():
    """End-to-end browser source against a local login portal.

    Skipped when Playwright/Chromium is unavailable in this environment.
    """
    try:
        from apps.fileflow.infra.browser_fetch import run_browser_source
    except Exception:
        pytest.skip("browser_fetch unavailable")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception:
        pytest.skip("Chromium unavailable")
    server = HTTPServer(("127.0.0.1", 0), _PortalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with tempfile.TemporaryDirectory() as tmp:
            path, name, url = run_browser_source(
                {
                    "actions": [
                        {"action": "goto", "url": base + "/login"},
                        {"action": "fill", "selector": "#user", "value": "alice"},
                        {"action": "fill", "selector": "#pass", "value": "secret"},
                        {"action": "click", "selector": "button[type=submit]"},
                        {"action": "wait_for", "selector": "a.download"},
                        {"action": "download", "selector": "a.download"},
                    ]
                },
                tmp,
                lambda m: None,
            )
            assert name == "portal-report.csv"
            assert open(path, encoding="utf-8").read().startswith("id,amount")
            assert url == base + "/login"
    finally:
        server.shutdown()
