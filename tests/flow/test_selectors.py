"""Tests for FileFlow v0.6.0 selectors: table rows, text, role, chain,
custom kinds — all against a real Chromium via a local table page."""
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from apps.fileflow.infra.browser_fetch import (
    SELECTOR_RESOLVERS,
    BrowserSourceError,
    register_selector,
    run_browser_source,
    validate_selector,
)

TABLE_HTML = """<table id="files"><thead><tr><th>Title</th><th>Date</th><th>Action</th></tr></thead>
<tbody>
<tr><td>Q1 Report</td><td>2026-01-01</td>
<td><a class="download" href="/q1.csv">Download Q1</a></td></tr>
<tr><td>Q3 Report</td><td>2026-07-01</td>
<td><a class="download" data-file="q3" href="/q3.csv">Download Q3</a></td></tr>
<tr><td>Q3 Report</td><td>2026-07-02</td>
<td><a class="download" href="/q3b.csv">Download Q3b</a></td></tr>
</tbody></table>
<button>Export all</button>
"""


class _TableHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/table":
            self._send(200, TABLE_HTML, "text/html")
        elif self.path in ("/q1.csv", "/q3.csv", "/q3b.csv"):
            name = self.path.strip("/")
            self._send(200, f"report,{name}\n", "text/csv", attachment=name)
        else:
            self._send(404, "not found", "text/plain")

    def _send(self, code, body, ctype, attachment=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)


def _chromium_available():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


def _run_table(actions, tmp):
    server = HTTPServer(("127.0.0.1", 0), _TableHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        full = [{"action": "goto", "url": base + "/table"}] + actions
        return run_browser_source({"actions": full}, tmp, lambda m: None)
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def chromium():
    if not _chromium_available():
        pytest.skip("Chromium unavailable")


def test_selector_validation():
    for good in (
        "a.download",
        {"css": "a.download"},
        {"kind": "text", "text": "Download"},
        {"kind": "role", "role": "button", "name": "Export all"},
        {"kind": "label", "label": "Username"},
        {"kind": "xpath", "xpath": "//a"},
        {"kind": "row", "column": "Title", "match": "Q3 Report", "target": "a.download"},
        {"kind": "row", "column": 0, "match": {"contains": "Q3"}, "nth": 1},
        {"kind": "chain", "steps": [{"css": "#files"}, {"css": "a.download"}]},
    ):
        assert validate_selector(good) == good
    for bad in (
        "",
        {"kind": "teleport", "text": "x"},
        {"kind": "row", "match": "x"},
        {"kind": "row", "column": "Title"},
        {"kind": "row", "column": "Title", "match": {"starts_with": "Q"}},
        {"kind": "row", "column": "Title", "match": "x", "nth": -1},
        {"kind": "chain", "steps": []},
        {"kind": "chain", "steps": [{"kind": "nope"}]},
        {"css": 42},
    ):
        with pytest.raises(BrowserSourceError):
            validate_selector(bad)


def test_row_by_header_equals(chromium):
    with tempfile.TemporaryDirectory() as tmp:
        path, name, _url = _run_table(
            [
                {
                    "action": "download",
                    "selector": {
                        "kind": "row",
                        "table": "#files",
                        "column": "Title",
                        "match": "Q3 Report",
                        "target": "a.download",
                    },
                }
            ],
            tmp,
        )
        assert name == "q3.csv"
        assert open(path, encoding="utf-8").read().startswith("report,q3.csv")


def test_row_by_index_contains_nth(chromium):
    with tempfile.TemporaryDirectory() as tmp:
        path, name, _url = _run_table(
            [
                {
                    "action": "download",
                    "selector": {
                        "kind": "row",
                        "column": 0,
                        "match": {"contains": "Q3"},
                        "nth": 1,
                        "target": "a.download",
                    },
                }
            ],
            tmp,
        )
        assert name == "q3b.csv"


def test_row_regex_and_text_and_role(chromium):
    with tempfile.TemporaryDirectory() as tmp:
        path, name, _url = _run_table(
            [
                {
                    "action": "wait_for",
                    "selector": {"kind": "role", "role": "button", "name": "Export all"},
                },
                {
                    "action": "download",
                    "selector": {
                        "kind": "row",
                        "column": "Title",
                        "match": {"regex": "^Q1"},
                        "target": {"text": "Download Q1"},
                    },
                },
            ],
            tmp,
        )
        assert name == "q1.csv"
    with tempfile.TemporaryDirectory() as tmp:
        # get_by_text is substring matching by default, so "Download Q3"
        # alone would also match "Download Q3b" — exact=True disambiguates.
        path, name, _url = _run_table(
            [{"action": "download", "selector": {"text": "Download Q3", "exact": True}}],
            tmp,
        )
        assert name == "q3.csv"


def test_row_no_match_error(chromium):
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(BrowserSourceError, match="no row matched"):
            _run_table(
                [
                    {
                        "action": "download",
                        "selector": {
                            "kind": "row",
                            "column": "Title",
                            "match": "Q9 Report",
                            "target": "a.download",
                        },
                    }
                ],
                tmp,
            )


def test_chain_and_custom_kind(chromium):
    with tempfile.TemporaryDirectory() as tmp:
        path, name, _url = _run_table(
            [
                {
                    "action": "download",
                    "selector": {
                        "kind": "chain",
                        "steps": [
                            {"kind": "row", "column": "Title", "match": "Q3 Report"},
                            {"css": "a.download"},
                        ],
                    },
                }
            ],
            tmp,
        )
        assert name == "q3.csv"
    register_selector(
        "data_file", lambda scope, spec: scope.locator(f"[data-file='{spec['data_file']}']")
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, name, _url = _run_table(
                [{"action": "download", "selector": {"kind": "data_file", "data_file": "q3"}}],
                tmp,
            )
            assert name == "q3.csv"
    finally:
        del SELECTOR_RESOLVERS["data_file"]
