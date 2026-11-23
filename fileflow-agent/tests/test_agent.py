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

from fileflow_contract import file_entry, verify_files, write_manifest

from fileflow_agent.adapters.cli import build_download_use_case, build_pickup_use_case
from fileflow_agent.infrastructure.config import parse_config

RECEIVED: list[bytes] = []


def _run_once(cfg: dict) -> dict:
    use_case, transport, repository = build_download_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        transport.close()
        repository.close()


def _pickup_once(cfg: dict) -> dict[str, int]:
    use_case, transport, repository = build_pickup_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        transport.close()
        repository.close()


class UploadHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # http.server naming
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        RECEIVED.append(body)
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


def _agent_cfg(tmp_path: Path, source: Path) -> dict:
    return {
        "job": "daily-report",
        "source": {"type": "local", "path": str(source)},
        "expect_csv": True,
        "procedure": "normalize-csv",
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
    }


def test_agent_uploads_file_and_manifest(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    result = _run_once(_agent_cfg(tmp_path, source))
    assert result["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["job"] == "daily-report"
    assert verify_files(incoming, manifest) == []
    assert list(incoming.glob("*.csv"))


def test_rerun_skips_already_processed_file(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    assert _run_once(cfg)["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert len(list(incoming.glob("*.manifest.json"))) == 1
    result = _run_once(cfg)
    assert result["status"] == "SKIPPED"
    assert result["skipped"] == ["daily.csv"]
    assert len(list(incoming.glob("*.manifest.json"))) == 1  # no second upload


def test_changed_file_is_processed_again(tmp_path: Path):
    drop = tmp_path / "drop"
    drop.mkdir()
    for name, text in (
        ("a.csv", " id , amount \n 1 , 10.5 \n"),
        ("b.csv", " id , amount \n 2 , 20 \n"),
        ("c.csv", " id , amount \n 3 , 30 \n"),
    ):
        (drop / name).write_text(text, encoding="utf-8")
    cfg = _agent_dir_cfg(tmp_path, drop)
    assert _run_once(cfg)["status"] == "UPLOADED"
    # Change one file: only it is fresh on the next run.
    (drop / "b.csv").write_text(" id , amount \n 2 , 21 \n", encoding="utf-8")
    result = _run_once(cfg)
    assert result["status"] == "UPLOADED"
    assert len(result["outputs"]) == 1
    assert sorted(result["skipped"]) == ["a.csv", "c.csv"]
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 2
    # Third run, nothing changed: skipped entirely.
    assert _run_once(cfg)["status"] == "SKIPPED"
    assert len(list(incoming.glob("*.manifest.json"))) == 2


def _agent_dir_cfg(tmp_path: Path, source_dir: Path) -> dict:
    cfg = _agent_cfg(tmp_path, source_dir / "placeholder.csv")
    cfg["source"] = {
        "type": "local-dir",
        "directory": str(source_dir),
        "pattern": "*.csv",
        "min_age_seconds": 0,
    }
    return cfg


def test_agent_uploads_batch_from_local_dir(tmp_path: Path):
    drop = tmp_path / "drop"
    drop.mkdir()
    contents = {
        "a.csv": " id , amount \n 1 , 10.5 \n",
        "b.csv": " id , amount \n 2 , 20 \n",
        "c.csv": " id , amount \n 3 , 30 \n",
    }
    for name, text in contents.items():
        (drop / name).write_text(text, encoding="utf-8")
    result = _run_once(_agent_dir_cfg(tmp_path, drop))
    assert result["status"] == "UPLOADED"
    assert len(result["outputs"]) == 3
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 1  # one batch, one manifest
    manifest = json.loads(manifests[0].read_text())
    assert len(manifest["files"]) == 3
    assert verify_files(incoming, manifest) == []
    # No manifest-level input hash: each file's hash is in files[],
    # and the run is identified by run_id alone.
    assert "input_sha256" not in manifest
    assert manifest["idempotency_key"] == f"daily-report|{result['run_id']}"
    assert len(list(incoming.glob("*.csv"))) == 3


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


def _seed_outgoing_batch(tmp_path: Path, files: dict[str, str]) -> Path:
    """Stage a multi-file batch in outgoing/ exactly as the watcher would."""
    out_dir = tmp_path / "ftp" / "outgoing" / "daily-report"
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, content in sorted(files.items()):
        data = out_dir / name
        data.write_text(content, encoding="utf-8")
        entries.append(file_entry(data, "output"))
    manifest = {
        "version": 1,
        "run_id": "fedcba9876543210",
        "job": "daily-report",
        "direction": "outbound",
        "idempotency_key": "daily-report|fedcba9876543210",
        "files": entries,
    }
    write_manifest(out_dir / "fedcba9876543210.manifest.json", manifest)
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


def test_pickup_uploads_to_website(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        _seed_outgoing(tmp_path)
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {"UPLOADED_TO_WEBSITE": 1}
        assert len(RECEIVED) == 1
        assert b"id,amount" in RECEIVED[0]
        assert list((tmp_path / "ftp" / "outgoing" / "daily-report").iterdir()) == []
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {}
    finally:
        server.shutdown()


def test_pickup_corrupt_file_is_not_uploaded(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        out_dir = _seed_outgoing(tmp_path)
        staged = out_dir / "to-upload.csv"
        staged.write_bytes(staged.read_bytes() + b"X")
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {"FAILED": 1}
        assert RECEIVED == []
        assert staged.exists()
    finally:
        server.shutdown()


def test_pickup_uploads_batch_in_one_post(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        _seed_outgoing_batch(
            tmp_path,
            {
                "part-a.csv": "id,amount\n1,10\n",
                "part-b.csv": "id,amount\n2,20\n",
                "part-c.csv": "id,amount\n3,30\n",
            },
        )
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {"UPLOADED_TO_WEBSITE": 1}
        assert len(RECEIVED) == 1  # one POST carrying all three files
        for marker in (b"id,amount\n1,10", b"id,amount\n2,20", b"id,amount\n3,30"):
            assert marker in RECEIVED[0]
        assert list((tmp_path / "ftp" / "outgoing" / "daily-report").iterdir()) == []
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {}
    finally:
        server.shutdown()


def test_pickup_waits_for_partial_batch(tmp_path: Path):
    RECEIVED.clear()
    server, port = _serve()
    try:
        out_dir = _seed_outgoing_batch(
            tmp_path,
            {
                "part-a.csv": "id,amount\n1,10\n",
                "part-b.csv": "id,amount\n2,20\n",
                "part-c.csv": "id,amount\n3,30\n",
            },
        )
        (out_dir / "part-c.csv").unlink()  # still in flight
        assert _pickup_once(_pickup_cfg(tmp_path, port)) == {}
        assert RECEIVED == []
        assert sorted(p.name for p in out_dir.iterdir()) == [
            "fedcba9876543210.manifest.json",
            "part-a.csv",
            "part-b.csv",
        ]
    finally:
        server.shutdown()


def test_rename_uses_job_variables_and_date_format(tmp_path: Path):
    # The filename render is the job's business logic: its own
    # variable and its own date format land in the output name.
    from datetime import datetime

    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    cfg["variables"] = {"client": "acme"}
    cfg["date_format"] = "%Y-%m-%d"
    cfg["rename_pattern"] = "{date}_{client}_{name}{ext}"
    result = _run_once(cfg)
    assert result["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    today = datetime.now().strftime("%Y-%m-%d")
    assert list(incoming.glob(f"{today}_acme_daily.csv"))
