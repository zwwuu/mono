"""Watcher tests: handoff (FTP 1 -> FTP 2) + completion (FTP 2
outcomes -> FTP 1 archive + email) + reverse flow (share
upload-requests -> FTP 1 outgoing). No agent code is imported
here -- the watcher is tested on its own. Incoming files are
seeded in the exact manifest format the agent produces, and FTP 2
is a second local folder standing in for the second server.
"""

import json
import shutil
import smtplib
import sys
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_contract import file_entry, write_manifest

from fileflow_watcher.adapters.cli import build_reconcile_use_case, build_scan_use_case
from fileflow_watcher.application.handoff import HandoffUseCase
from fileflow_watcher.infrastructure.config import parse_config
from fileflow_watcher.infrastructure.persistence import SqlAlchemyRunRepository
from fileflow_watcher.infrastructure.transport import LocalTransport


class FakeSMTP:
    """Captures sent mail; stands in for smtplib.SMTP in tests."""

    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


class RecordingTransport(LocalTransport):
    """A local transport that records what the use case asked it to do."""

    def __init__(self, root):
        super().__init__(root)
        self.calls: list[tuple] = []

    def upload(self, local_path, remote_dir, remote_name):
        self.calls.append(("upload", remote_dir, remote_name))
        return super().upload(local_path, remote_dir, remote_name)

    def makedirs(self, remote_dir):
        self.calls.append(("makedirs", remote_dir))
        return super().makedirs(remote_dir)

    def move(self, remote_dir, remote_name, dest_dir, dest_name):
        self.calls.append(("move", remote_dir, remote_name, dest_dir, dest_name))
        return super().move(remote_dir, remote_name, dest_dir, dest_name)


def _scan_once(cfg: dict) -> dict[str, int]:
    scan, repository, transports = build_scan_use_case(parse_config(cfg))
    try:
        return scan.run()
    finally:
        repository.close()
        for t in transports:
            t.close()


def _reconcile(cfg: dict) -> list[str]:
    use_case, repository = build_reconcile_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        repository.close()


def _ledger_run(tmp_path: Path, run_id: str):
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    try:
        return repo.get(run_id)
    finally:
        repo.close()


def _watcher_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "intake": {"transport": "local", "root": str(tmp_path / "ftp1")},
        "processing": {"transport": "local", "root": str(tmp_path / "ftp2")},
        "jobs": ["daily-report"],
        "share_root": str(tmp_path / "share"),
        "ledger_db": str(tmp_path / "watcher.db"),
        "notify": {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from": "watcher@example.com",
            "to": "ops@example.com",
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
    job: str = "daily-report",
    files: dict | None = None,
) -> Path:
    """Seed one manifest batch in FTP 1 incoming/<job>/, exactly as
    the agent seals one: data files + <run_id>.manifest.json."""
    files = files or {"report.csv": "id,amount\n1,10.5\n"}
    incoming = tmp_path / "ftp1" / "incoming" / job
    incoming.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, text in sorted(files.items()):
        data = incoming / name
        data.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        entries.append(file_entry(data, "output"))
    manifest = {
        "version": 1,
        "run_id": run_id,
        "job": job,
        "idempotency_key": f"{job}|{run_id}",
        "files": entries,
    }
    write_manifest(incoming / f"{run_id}.manifest.json", manifest)
    return incoming


def _processor_finish(tmp_path: Path, name: str, outcome: str, content: str | None = None) -> None:
    """Simulate the external processor: move a handed-off file from
    FTP 2 processing/ to success/ or failed/ (optionally rewriting
    its bytes, for the hash-mismatch case)."""
    src = tmp_path / "ftp2" / "processing" / "daily-report" / name
    dest_dir = tmp_path / "ftp2" / outcome / "daily-report"
    dest_dir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        (dest_dir / name).write_text(content, encoding="utf-8")
        src.unlink()
    else:
        shutil.move(str(src), str(dest_dir / name))


# -- handoff -------------------------------------------------------------


def test_handoff_copies_verified_batch_to_ftp2(tmp_path: Path):
    _seed_incoming(tmp_path, files={"a.csv": "id,amount\n1,10\n", "b.csv": "id,amount\n2,20\n"})
    assert _scan_once(_watcher_cfg(tmp_path)) == {"SENT_TO_PROCESSING": 1}
    proc = tmp_path / "ftp2" / "processing" / "daily-report"
    assert {p.name for p in proc.iterdir()} == {"a.csv", "b.csv"}
    assert not list(proc.glob("*.manifest.json"))  # the manifest is never copied
    assert (
        tmp_path / "ftp1" / "incoming" / "daily-report" / "0123456789abcdef.manifest.json"
    ).exists()
    record = _ledger_run(tmp_path, "0123456789abcdef")
    assert record.state == "SENT_TO_PROCESSING"
    assert [f["name"] for f in record.detail["files"]] == ["a.csv", "b.csv"]
    assert FakeSMTP.sent == []  # no email until the batch completes


def test_incomplete_manifest_is_left_alone(tmp_path: Path):
    incoming = _seed_incoming(tmp_path)
    (incoming / "report.csv").unlink()
    assert _scan_once(_watcher_cfg(tmp_path)) == {}
    assert (incoming / "0123456789abcdef.manifest.json").exists()
    assert not (tmp_path / "ftp2").exists()


def test_corrupt_file_is_quarantined(tmp_path: Path):
    incoming = _seed_incoming(tmp_path)
    (incoming / "report.csv").write_bytes(b"corrupted")
    assert _scan_once(_watcher_cfg(tmp_path)) == {"QUARANTINED": 1}
    quarantine = tmp_path / "ftp1" / "quarantine" / "0123456789abcdef"
    assert (quarantine / "report.csv").exists()
    assert (quarantine / "0123456789abcdef.manifest.json").exists()
    assert (quarantine / "reason.txt").read_text(encoding="utf-8")
    assert not (tmp_path / "ftp2").exists()
    assert _ledger_run(tmp_path, "0123456789abcdef").state == "QUARANTINED"


def test_dedup_skip_moves_to_done(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    # Re-drop the exact same file under a new run_id.
    _seed_incoming(tmp_path, run_id="aaaaaaaaaaaaaaaa")
    assert _scan_once(cfg) == {"DUPLICATE": 1}
    assert (tmp_path / "ftp1" / "done" / "daily-report" / "report.csv").exists()
    assert (tmp_path / "ftp1" / "done" / "daily-report" / "aaaaaaaaaaaaaaaa.manifest.json").exists()
    # FTP 2 still has exactly the one copy from the first handoff.
    assert len(list((tmp_path / "ftp2" / "processing" / "daily-report").iterdir())) == 1
    assert FakeSMTP.sent == []


def test_changed_file_copies_again(tmp_path: Path):
    _seed_incoming(tmp_path, files={"report.csv": "id,amount\n1,10\n"})
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _seed_incoming(tmp_path, run_id="bbbbbbbbbbbbbbbb", files={"report.csv": "id,amount\n1,99\n"})
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    assert (tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv").read_text(
        encoding="utf-8"
    ) == "id,amount\n1,99\n"


def test_unknown_job_is_dead_lettered(tmp_path: Path):
    # A manifest for a job that is not in `jobs` is NOT a config
    # error: the watcher dead-letters it with the reason attached,
    # never guessing a route.
    _seed_incoming(tmp_path, job="no-such-job")
    assert _scan_once(_watcher_cfg(tmp_path)) == {"FAILED": 1}
    failed = tmp_path / "ftp1" / "failed" / "0123456789abcdef"
    assert (failed / "reason.txt").read_text(encoding="utf-8") == "unknown job 'no-such-job'"


def test_handoff_goes_through_transports(tmp_path: Path):
    _seed_incoming(tmp_path, files={"a.csv": "x", "b.csv": "y"})
    cfg = parse_config(_watcher_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    intake = LocalTransport(str(tmp_path / "ftp1"))
    processing = RecordingTransport(str(tmp_path / "ftp2"))
    try:
        use_case = HandoffUseCase(cfg, repository=repo, intake=intake, processing=processing)
        assert (
            use_case.handoff("daily-report", "0123456789abcdef.manifest.json")
            == "SENT_TO_PROCESSING"
        )
    finally:
        repo.close()
    uploads = [c for c in processing.calls if c[0] == "upload"]
    assert uploads == [
        ("upload", "processing/daily-report", "a.csv"),
        ("upload", "processing/daily-report", "b.csv"),
    ]
    assert ("makedirs", "processing/daily-report") in processing.calls
    assert not any(c[2].endswith(".manifest.json") for c in uploads)


# -- completion ----------------------------------------------------------


def test_completion_success_archives_and_emails(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "success")
    assert _scan_once(cfg) == {"COMPLETED": 1}
    assert (tmp_path / "ftp1" / "done" / "daily-report" / "report.csv").exists()
    assert (tmp_path / "ftp1" / "done" / "daily-report" / "0123456789abcdef.manifest.json").exists()
    assert _ledger_run(tmp_path, "0123456789abcdef").state == "COMPLETED"
    assert len(FakeSMTP.sent) == 1
    msg = FakeSMTP.sent[0]
    assert "completed" in msg["Subject"]
    assert "report.csv" in msg.get_content()
    assert msg["To"] == "ops@example.com"


def test_completion_failure_archives_and_emails(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "failed")
    assert _scan_once(cfg) == {"FAILED": 1}
    assert (tmp_path / "ftp1" / "failed" / "daily-report" / "report.csv").exists()
    assert (
        tmp_path / "ftp1" / "failed" / "daily-report" / "0123456789abcdef.manifest.json"
    ).exists()
    assert _ledger_run(tmp_path, "0123456789abcdef").state == "FAILED"
    assert len(FakeSMTP.sent) == 1
    assert "FAILED" in FakeSMTP.sent[0]["Subject"]
    assert "report.csv" in FakeSMTP.sent[0].get_content()


def test_completion_hash_mismatch_is_failure(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "success", content="different bytes")
    assert _scan_once(cfg) == {"FAILED": 1}
    assert (tmp_path / "ftp1" / "failed" / "daily-report" / "report.csv").exists()
    assert len(FakeSMTP.sent) == 1
    assert "hash mismatch" in FakeSMTP.sent[0].get_content()


def test_unknown_finished_file_left_alone(tmp_path: Path):
    unknown = tmp_path / "ftp2" / "success" / "daily-report" / "stray.csv"
    unknown.parent.mkdir(parents=True)
    unknown.write_text("x", encoding="utf-8")
    assert _scan_once(_watcher_cfg(tmp_path)) == {}
    assert unknown.exists()  # never deleted, never acted on
    assert FakeSMTP.sent == []


def test_mixed_outcome_batch_moves_manifest_to_failed(tmp_path: Path):
    _seed_incoming(tmp_path, files={"a.csv": "id\n1\n", "b.csv": "id\n2\n"})
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "a.csv", "success")
    _processor_finish(tmp_path, "b.csv", "failed")
    assert _scan_once(cfg) == {"FAILED": 1}
    assert (tmp_path / "ftp1" / "done" / "daily-report" / "a.csv").exists()
    assert (tmp_path / "ftp1" / "failed" / "daily-report" / "b.csv").exists()
    assert (
        tmp_path / "ftp1" / "failed" / "daily-report" / "0123456789abcdef.manifest.json"
    ).exists()
    assert _ledger_run(tmp_path, "0123456789abcdef").state == "FAILED"
    assert len(FakeSMTP.sent) == 1
    body = FakeSMTP.sent[0].get_content()
    assert "success: a.csv" in body
    assert "failed: b.csv" in body


def test_completion_without_notify_logs_instead(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path, notify=None)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "success")
    assert _scan_once(cfg) == {"COMPLETED": 1}
    assert FakeSMTP.sent == []


def test_reconcile_reports_inflight(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    lines = _reconcile(cfg)
    assert len(lines) == 1
    assert "0123456789abcdef" in lines[0]
    assert "daily-report" in lines[0]
    assert "report.csv" in lines[0]
    assert "waiting" in lines[0]
    _processor_finish(tmp_path, "report.csv", "success")
    assert _scan_once(cfg) == {"COMPLETED": 1}
    assert _reconcile(cfg) == []


# -- reverse flow --------------------------------------------------------


def test_reverse_stages_share_file_to_outgoing(tmp_path: Path):
    dropped = tmp_path / "share" / "upload-requests" / "daily-report" / "to-upload.csv"
    dropped.parent.mkdir(parents=True, exist_ok=True)
    dropped.write_text("id,amount\n7,99\n", encoding="utf-8")
    assert _scan_once(_watcher_cfg(tmp_path)) == {"OUTBOUND_READY": 1}
    outgoing = tmp_path / "ftp1" / "outgoing" / "daily-report"
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
    outgoing = tmp_path / "ftp1" / "outgoing" / "daily-report"
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
