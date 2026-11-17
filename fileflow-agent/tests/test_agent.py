"""Agent tests: download/upload to FTP, and pickup from outgoing to website.

No watcher code is imported here -- the agent is tested on its own.
The FTP server is simulated with the "local" transport (plain folders)
and the website with a tiny local HTTP server.
"""
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_agent.agent import pickup_once, run_once
from fileflow_agent.common import file_entry, verify_files, write_manifest

RECEIVED: list[bytes] = []


class UploadHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server naming
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        RECEIVED.append(body)
        start = body.find(b'filename="')
        header_end = body.find(b"\r\n\r\n", start)
        content = body[header_end + 4: body.rfind(b"\r\n--")]
        payload = json.dumps(
            {"sha256": hashlib.sha256(content).hexdigest(),
             "size": len(content)}).encode()
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


def _agent_cfg(tmp_path: Path, source: Path) -> dict:
    return {
        "job": "daily-report",
        "source": {"type": "local", "path": str(source)},
        "expect_csv": True,
        "agent_procedure": "normalize-csv",
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "ftp": {"transport": "local", "root": str(tmp_path / "ftp")},
    }


def test_agent_uploads_file_and_manifest(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    result = run_once(_agent_cfg(tmp_path, source))
    assert result["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["job"] == "daily-report"
    assert verify_files(incoming, manifest) == []
    assert list(incoming.glob("*.csv"))


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
        "idempotency_key": "outbound|daily-report|"
                           + hashlib.sha256(content.encode()).hexdigest()
                           + "|to-upload.csv",
        "input_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "files": [file_entry(data, "output")],
    }
    write_manifest(out_dir / "to-upload.manifest.json", manifest)
    return out_dir


def _pickup_cfg(tmp_path: Path, port: int) -> dict:
    return {
        "job": "daily-report",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "ftp": {"transport": "local", "root": str(tmp_path / "ftp")},
        "pickup": {"jobs": ["daily-report"],
                   "upload_url": f"http://127.0.0.1:{port}/upload",
                   "field": "file"},
    }


def test_pickup_uploads_to_website(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        _seed_outgoing(tmp_path)
        assert pickup_once(_pickup_cfg(tmp_path, port)) == {"UPLOADED_TO_WEBSITE": 1}
        assert len(RECEIVED) == 1
        assert b"id,amount" in RECEIVED[0]
        assert list((tmp_path / "ftp" / "outgoing" / "daily-report").iterdir()) == []
        assert pickup_once(_pickup_cfg(tmp_path, port)) == {}
    finally:
        server.shutdown()


def test_pickup_corrupt_file_is_not_uploaded(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        out_dir = _seed_outgoing(tmp_path)
        staged = out_dir / "to-upload.csv"
        staged.write_bytes(staged.read_bytes() + b"X")
        assert pickup_once(_pickup_cfg(tmp_path, port)) == {"FAILED": 1}
        assert RECEIVED == []
        assert staged.exists()
    finally:
        server.shutdown()
