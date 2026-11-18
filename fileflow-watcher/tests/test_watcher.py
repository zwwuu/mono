"""Watcher tests: forward flow (incoming -> share) and reverse flow
(share upload-requests -> outgoing). No agent code is imported here --
the watcher is tested on its own. Incoming files are seeded in the
exact manifest format the agent produces.
"""

import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_watcher.adapters.cli import build_reconcile_use_case, build_scan_use_case
from fileflow_watcher.infrastructure.filesystem import file_entry, write_manifest


def _scan_once(cfg: dict) -> dict[str, int]:
    scan, repository = build_scan_use_case(cfg)
    try:
        return scan.run()
    finally:
        repository.close()


def _reconcile(cfg: dict) -> list[str]:
    use_case, repository = build_reconcile_use_case(cfg)
    try:
        return use_case.run()
    finally:
        repository.close()


def _watcher_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "ftp_root": str(tmp_path / "ftp"),
        "share_root": str(tmp_path / "share"),
        "ledger_db": str(tmp_path / "watcher.db"),
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
            "min_age_seconds": 0,
        },
    }
    cfg.update(extra)
    return cfg


def _seed_incoming(
    tmp_path: Path,
    run_id: str = "0123456789abcdef",
    content: str = " id , amount \n 1 , 10.5 \n\n2,20\n",
) -> Path:
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    incoming.mkdir(parents=True, exist_ok=True)
    data = incoming / "20261117_daily.csv"
    data.write_text(content, encoding="utf-8")
    manifest = {
        "version": 1,
        "run_id": run_id,
        "job": "daily-report",
        "idempotency_key": f"daily-report|{run_id}",
        "files": [file_entry(data, "output")],
    }
    write_manifest(incoming / "20261117_daily.manifest.json", manifest)
    return incoming


def test_forward_publishes_to_share(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"PUBLISHED": 1}
    published = list((tmp_path / "share" / "reports" / "daily").glob("*.csv"))
    assert len(published) == 1
    with open(published[0], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["id", "amount"]
    assert len(rows) == 3  # header + 2 rows; empty row dropped
    assert list((tmp_path / "share" / "reports" / "daily").glob("*.manifest.json"))
    assert _reconcile(cfg) == []
    assert list((tmp_path / "ftp" / "done").iterdir())


def _seed_incoming_batch(tmp_path: Path, run_id: str = "fedcba9876543210") -> Path:
    """Seed a 3-file forward batch, exactly as the agent seals one."""
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    incoming.mkdir(parents=True, exist_ok=True)
    contents = {
        "20261117_a.csv": " id , amount \n 1 , 10.5 \n",
        "20261117_b.csv": " id , amount \n 2 , 20 \n",
        "20261117_c.csv": " id , amount \n 3 , 30 \n",
    }
    entries = []
    for name, text in sorted(contents.items()):
        data = incoming / name
        data.write_text(text, encoding="utf-8")
        entries.append(file_entry(data, "output"))
    manifest = {
        "version": 1,
        "run_id": run_id,
        "job": "daily-report",
        "idempotency_key": f"daily-report|{run_id}",
        "files": entries,
    }
    write_manifest(incoming / f"{run_id}.manifest.json", manifest)
    return incoming


def test_forward_publishes_batch_to_share(tmp_path: Path):
    _seed_incoming_batch(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"PUBLISHED": 1}
    share = tmp_path / "share" / "reports" / "daily"
    published = list(share.glob("*.csv"))
    assert len(published) == 3
    for p in published:
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["id", "amount"]
        assert len(rows) == 2  # header + 1 row
    manifests = list(share.glob("*.manifest.json"))
    assert len(manifests) == 1  # one batch, one manifest
    manifest = json.loads(manifests[0].read_text())
    assert len(manifest["files"]) == 3
    assert _reconcile(cfg) == []
    assert list((tmp_path / "ftp" / "done").iterdir())


def test_corrupt_file_is_quarantined(tmp_path: Path):
    incoming = _seed_incoming(tmp_path)
    (incoming / "20261117_daily.csv").write_bytes(b"corrupted")
    assert _scan_once(_watcher_cfg(tmp_path)) == {"QUARANTINED": 1}
    assert not list((tmp_path / "share").rglob("*.csv"))
    assert list((tmp_path / "ftp" / "quarantine").iterdir())


def test_duplicate_is_not_republished(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"PUBLISHED": 1}
    done = next((tmp_path / "ftp" / "done").iterdir())
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    incoming.mkdir(parents=True, exist_ok=True)
    for p in done.iterdir():
        if p.suffix in (".csv", ".json"):
            shutil.copyfile(p, incoming / p.name)
    assert _scan_once(cfg) == {"DUPLICATE": 1}
    assert len(list((tmp_path / "share" / "reports" / "daily").glob("*.csv"))) == 1


def test_reverse_stages_share_file_to_outgoing(tmp_path: Path):
    dropped = tmp_path / "share" / "upload-requests" / "daily-report" / "to-upload.csv"
    dropped.parent.mkdir(parents=True, exist_ok=True)
    dropped.write_text("id,amount\n7,99\n", encoding="utf-8")
    assert _scan_once(_watcher_cfg(tmp_path)) == {"OUTBOUND_READY": 1}
    outgoing = tmp_path / "ftp" / "outgoing" / "daily-report"
    manifests = list(outgoing.glob("*.manifest.json"))
    assert len(manifests) == 1
    assert {p.name for p in outgoing.iterdir()} == {"to-upload.csv", manifests[0].name}
    manifest = json.loads(manifests[0].read_text())
    assert [f["name"] for f in manifest["files"]] == ["to-upload.csv"]
    assert not dropped.exists()
    assert (tmp_path / "share" / "upload-archive" / "daily-report" / "to-upload.csv").exists()


def test_reverse_batches_multiple_files_into_one_job(tmp_path: Path):
    requests = tmp_path / "share" / "upload-requests" / "daily-report"
    requests.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("part-a.csv", "id,amount\n1,10\n"),
        ("part-b.csv", "id,amount\n2,20\n"),
        ("part-c.csv", "id,amount\n3,30\n"),
    ):
        (requests / name).write_text(content, encoding="utf-8")
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"OUTBOUND_READY": 1}
    outgoing = tmp_path / "ftp" / "outgoing" / "daily-report"
    manifests = list(outgoing.glob("*.manifest.json"))
    assert len(manifests) == 1  # one batch, one manifest, one run
    manifest = json.loads(manifests[0].read_text())
    assert sorted(f["name"] for f in manifest["files"]) == [
        "part-a.csv",
        "part-b.csv",
        "part-c.csv",
    ]
    assert {p.name for p in outgoing.iterdir()} == {
        "part-a.csv",
        "part-b.csv",
        "part-c.csv",
        manifests[0].name,
    }
    for name in ("part-a.csv", "part-b.csv", "part-c.csv"):
        assert not (requests / name).exists()
        assert (tmp_path / "share" / "upload-archive" / "daily-report" / name).exists()
    assert _scan_once(cfg) == {}  # nothing left to stage
    # Re-dropping a file from the finished batch is a duplicate, not a new job.
    (requests / "part-a.csv").write_text("id,amount\n1,10\n", encoding="utf-8")
    assert _scan_once(cfg) == {"DUPLICATE": 1}
    assert len(list(outgoing.glob("*.manifest.json"))) == 1
