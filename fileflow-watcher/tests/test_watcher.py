"""Watcher tests: forward flow (incoming -> share) and reverse flow
(share upload-requests -> outgoing). No agent code is imported here --
the watcher is tested on its own. Incoming files are seeded in the
exact manifest format the agent produces.
"""
import csv
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_watcher.common import file_entry, write_manifest
from fileflow_watcher.watcher import reconcile, scan_once


def _watcher_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "ftp_root": str(tmp_path / "ftp"),
        "share_root": str(tmp_path / "share"),
        "ledger_db": str(tmp_path / "watcher.db"),
        "jobs": {"daily-report": {"procedure": "normalize-csv",
                                   "expect_csv": True,
                                   "share_dir": "reports/daily",
                                   "outbound": True}},
        "outbound": {"requests_dir": "upload-requests",
                     "archive_dir": "upload-archive",
                     "min_age_seconds": 0},
    }
    cfg.update(extra)
    return cfg


def _seed_incoming(tmp_path: Path, run_id: str = "0123456789abcdef",
                   content: str = " id , amount \n 1 , 10.5 \n\n2,20\n") -> Path:
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    incoming.mkdir(parents=True, exist_ok=True)
    data = incoming / "20261117_daily.csv"
    data.write_text(content, encoding="utf-8")
    manifest = {
        "version": 1,
        "run_id": run_id,
        "job": "daily-report",
        "idempotency_key": f"daily-report|{run_id}|"
                           + hashlib.sha256(content.encode()).hexdigest(),
        "input_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "files": [file_entry(data, "output")],
    }
    write_manifest(incoming / "20261117_daily.manifest.json", manifest)
    return incoming


def test_forward_publishes_to_share(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert scan_once(cfg) == {"PUBLISHED": 1}
    published = list((tmp_path / "share" / "reports" / "daily").glob("*.csv"))
    assert len(published) == 1
    with open(published[0], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["id", "amount"]
    assert len(rows) == 3  # header + 2 rows; empty row dropped
    assert list((tmp_path / "share" / "reports" / "daily").glob("*.manifest.json"))
    assert reconcile(cfg) == []
    assert list((tmp_path / "ftp" / "done").iterdir())


def test_corrupt_file_is_quarantined(tmp_path: Path):
    incoming = _seed_incoming(tmp_path)
    (incoming / "20261117_daily.csv").write_bytes(b"corrupted")
    assert scan_once(_watcher_cfg(tmp_path)) == {"QUARANTINED": 1}
    assert not list((tmp_path / "share").rglob("*.csv"))
    assert list((tmp_path / "ftp" / "quarantine").iterdir())


def test_duplicate_is_not_republished(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert scan_once(cfg) == {"PUBLISHED": 1}
    done = next((tmp_path / "ftp" / "done").iterdir())
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    incoming.mkdir(parents=True, exist_ok=True)
    for p in done.iterdir():
        if p.suffix in (".csv", ".json"):
            shutil.copyfile(p, incoming / p.name)
    assert scan_once(cfg) == {"DUPLICATE": 1}
    assert len(list((tmp_path / "share" / "reports" / "daily").glob("*.csv"))) == 1


def test_reverse_stages_share_file_to_outgoing(tmp_path: Path):
    dropped = (tmp_path / "share" / "upload-requests" / "daily-report"
               / "to-upload.csv")
    dropped.parent.mkdir(parents=True, exist_ok=True)
    dropped.write_text("id,amount\n7,99\n", encoding="utf-8")
    assert scan_once(_watcher_cfg(tmp_path)) == {"OUTBOUND_READY": 1}
    outgoing = tmp_path / "ftp" / "outgoing" / "daily-report"
    assert {p.name for p in outgoing.iterdir()} == {
        "to-upload.csv", "to-upload.manifest.json"}
    assert not dropped.exists()
    assert (tmp_path / "share" / "upload-archive" / "daily-report"
            / "to-upload.csv").exists()
