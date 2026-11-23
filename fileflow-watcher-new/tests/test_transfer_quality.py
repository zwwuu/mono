"""Transfer-quality tests (watcher): hash-verified uploads,
record-first handoff with resume, retry with backoff, stuck-file
alerts, parallel multi-file uploads, per-run summaries.

Transport fakes subclass LocalTransport so the real FTP code is
never involved; the retry helper and the use cases are tested
with no-op sleeps so no test ever waits.
"""

import logging
import smtplib
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_watcher import (  # helpers shared with test_watcher.py
    FakeSMTP,
    _ledger_run,
    _processor_finish,
    _scan_once,
    _seed_incoming,
    _watcher_cfg,
)

from fileflow_watcher.application.handoff import HandoffUseCase
from fileflow_watcher.application.stale import StaleAlertUseCase
from fileflow_watcher.infrastructure.config import parse_config
from fileflow_watcher.infrastructure.notify import Notifier
from fileflow_watcher.infrastructure.persistence import SqlAlchemyRunRepository
from fileflow_watcher.infrastructure.retry import with_retries
from fileflow_watcher.infrastructure.transport import LocalTransport


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


class FakeNotifier:
    """Records notify calls; returns True (sent) by default."""

    cfg = None

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def notify(self, subject: str, body: str) -> bool:
        self.calls.append((subject, body))
        return True


class FlakyNotifier(FakeNotifier):
    """Fails the first send, then succeeds."""

    def __init__(self):
        super().__init__()
        self.attempts = 0

    def notify(self, subject: str, body: str) -> bool:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("mail server down")
        return super().notify(subject, body)


# -- (1) hash-verified uploads -------------------------------------------


class TamperingTransport(LocalTransport):
    """Corrupts what landed (same size, different bytes) after
    every upload -- the size check passes, the hash check must not."""

    def upload(self, local_path, remote_dir, remote_name):
        super().upload(local_path, remote_dir, remote_name)
        p = self._remote(remote_dir, remote_name)
        data = bytearray(p.read_bytes())
        data[0] ^= 0xFF
        p.write_bytes(bytes(data))


class TruncatingTransport(LocalTransport):
    """Shortens what landed after every upload."""

    def upload(self, local_path, remote_dir, remote_name):
        super().upload(local_path, remote_dir, remote_name)
        p = self._remote(remote_dir, remote_name)
        p.write_bytes(p.read_bytes()[:1])


def test_hash_mismatch_after_upload_raises_and_no_manifest(tmp_path: Path):
    transport = TamperingTransport(tmp_path / "ftp")
    data = tmp_path / "a.csv"
    data.write_text("x", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        transport.upload_many_with_manifest([(data, "a.csv")], manifest, "outgoing/job")
    assert not (tmp_path / "ftp" / "outgoing" / "job" / "r.manifest.json").exists()


def test_size_mismatch_after_upload_raises_and_no_manifest(tmp_path: Path):
    transport = TruncatingTransport(tmp_path / "ftp")
    data = tmp_path / "a.csv"
    data.write_text("id,amount\n1,10\n", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError, match="size mismatch"):
        transport.upload_many_with_manifest([(data, "a.csv")], manifest, "outgoing/job")
    assert not (tmp_path / "ftp" / "outgoing" / "job" / "r.manifest.json").exists()


def test_handoff_records_before_copy_even_when_copy_fails(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = parse_config(_watcher_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    try:
        use_case = HandoffUseCase(
            cfg,
            repository=repo,
            intake=LocalTransport(tmp_path / "ftp1"),
            processing=TamperingTransport(tmp_path / "ftp2"),
        )
        # The copy fails the hash check, so the handoff returns None
        # -- but the run was already recorded SENT_TO_PROCESSING
        # before the copy, so the next scan can resume it.
        assert use_case.handoff("daily-report", "0123456789abcdef.manifest.json") is None
    finally:
        repo.close()
    record = _ledger_run(tmp_path, "0123456789abcdef")
    assert record.state == "SENT_TO_PROCESSING"
    assert [f["name"] for f in record.detail["files"]] == ["report.csv"]


# -- (2) record-first handoff, idempotent resume --------------------------


def test_resuming_run_with_files_present_copies_nothing(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    landed = tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv"
    before = landed.read_bytes()
    before_mtime = landed.stat().st_mtime_ns
    # The manifest is still in incoming/ (it only moves when the
    # run finishes), so the next scan sees the run again -- but
    # every file is already on FTP 2 with the same hash, so nothing
    # is re-copied (the mtime proves the file was not rewritten).
    assert _scan_once(cfg) == {}
    assert landed.read_bytes() == before
    assert landed.stat().st_mtime_ns == before_mtime


def test_resuming_run_recopies_a_missing_file(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    landed = tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv"
    landed.unlink()  # the crashed pass never copied it
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    assert landed.exists()
    assert landed.read_text(encoding="utf-8") == "id,amount\n1,10.5\n"


def test_resuming_run_recopies_same_size_changed_file(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    landed = tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv"
    # Same size, different bytes on FTP 2: the resume must not
    # trust the size -- it downloads the remote file back, the
    # hash differs from the recorded one, and the recorded content
    # is re-copied from FTP 1.
    landed.write_text("id,amount\n9,99.9\n", encoding="utf-8")
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    assert landed.read_text(encoding="utf-8") == "id,amount\n1,10.5\n"


def test_resuming_run_does_not_recopy_a_finished_file(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "success")
    # The file is in FTP 2 success/ now, awaiting the completion
    # scan: the handoff must not put it back in processing/.
    assert _scan_once(cfg) == {"COMPLETED": 1}
    assert not (tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv").exists()


# -- (3) retry with backoff ----------------------------------------------


def test_with_retries_succeeds_after_transient_failures():
    calls: list[int] = []
    sleeps: list[float] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("blip")
        return "ok"

    assert with_retries(flaky, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff


def test_with_retries_raises_after_exhausting_attempts():
    calls: list[int] = []
    sleeps: list[float] = []

    def always_down():
        calls.append(1)
        raise OSError("down")

    with pytest.raises(OSError, match="down"):
        with_retries(always_down, sleep=sleeps.append)
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_with_retries_never_retries_validation_errors():
    calls: list[int] = []

    def bad_input():
        calls.append(1)
        raise ValueError("not a csv")

    with pytest.raises(ValueError, match="not a csv"):
        with_retries(bad_input, sleep=lambda s: None)
    assert len(calls) == 1  # failed immediately, no retry


class FlakyIntakeTransport(LocalTransport):
    """Fails the first N downloads with OSError, then works."""

    def __init__(self, root, fail_times: int = 2):
        super().__init__(root)
        self.fail_times = fail_times
        self.download_calls = 0

    def download(self, remote_dir, remote_name, local_path):
        self.download_calls += 1
        if self.download_calls <= self.fail_times:
            raise OSError("simulated blip")
        return super().download(remote_dir, remote_name, local_path)


def test_handoff_retries_transient_intake_failure(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = parse_config(_watcher_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    intake = FlakyIntakeTransport(tmp_path / "ftp1", fail_times=2)
    sleeps: list[float] = []
    try:
        use_case = HandoffUseCase(
            cfg,
            repository=repo,
            intake=intake,
            processing=LocalTransport(tmp_path / "ftp2"),
            sleep=sleeps.append,
        )
        assert (
            use_case.handoff("daily-report", "0123456789abcdef.manifest.json")
            == "SENT_TO_PROCESSING"
        )
    finally:
        repo.close()
    assert sleeps == [1.0, 2.0]
    assert (tmp_path / "ftp2" / "processing" / "daily-report" / "report.csv").exists()


def test_handoff_gives_up_after_exhausting_retries(tmp_path: Path):
    _seed_incoming(tmp_path)
    cfg = parse_config(_watcher_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    intake = FlakyIntakeTransport(tmp_path / "ftp1", fail_times=99)
    sleeps: list[float] = []
    try:
        use_case = HandoffUseCase(
            cfg,
            repository=repo,
            intake=intake,
            processing=LocalTransport(tmp_path / "ftp2"),
            sleep=sleeps.append,
        )
        assert use_case.handoff("daily-report", "0123456789abcdef.manifest.json") is None
    finally:
        repo.close()
    assert intake.download_calls == 3  # 3 attempts, then give up
    assert sleeps == [1.0, 2.0]
    assert _ledger_run(tmp_path, "0123456789abcdef") is None


# -- (4) stuck-file alert -------------------------------------------------


def _make_stale_run(tmp_path: Path, *, hours: float) -> datetime:
    """Hand a batch off, then backdate its sent_at by `hours`."""
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path, stale_after_seconds=3600)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=hours)
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    try:
        repo.set_state("0123456789abcdef", "SENT_TO_PROCESSING", sent_at=old.isoformat())
    finally:
        repo.close()
    return now


def _stale_use_case(tmp_path: Path, notifier, **extra):
    cfg = parse_config(_watcher_cfg(tmp_path, stale_after_seconds=3600, **extra))
    repo = SqlAlchemyRunRepository(tmp_path / "watcher.db")
    return StaleAlertUseCase(cfg, repository=repo, notifier=notifier), repo


def test_stale_alert_sent_once_then_not_again(tmp_path: Path):
    now = _make_stale_run(tmp_path, hours=25)
    notifier = FakeNotifier()
    use_case, repo = _stale_use_case(tmp_path, notifier)
    try:
        assert use_case.run(now=now) == 1
        assert len(notifier.calls) == 1
        subject, body = notifier.calls[0]
        assert "report.csv" in subject
        assert "daily-report" in body
        assert "0123456789abcdef" in body
        assert "waiting:" in body
        # Second scan: the per-file flag is set, so no second email.
        assert use_case.run(now=now) == 0
        assert len(notifier.calls) == 1
    finally:
        repo.close()
    assert _ledger_run(tmp_path, "0123456789abcdef").detail["stale_alerted"] == ["report.csv"]


def test_fresh_run_gets_no_stale_alert(tmp_path: Path):
    now = _make_stale_run(tmp_path, hours=1)  # under the 1h threshold
    notifier = FakeNotifier()
    use_case, repo = _stale_use_case(tmp_path, notifier)
    try:
        assert use_case.run(now=now) == 0
        assert notifier.calls == []
    finally:
        repo.close()


def test_stale_alert_without_notify_logs_error(tmp_path: Path, caplog):
    now = _make_stale_run(tmp_path, hours=25)
    use_case, repo = _stale_use_case(tmp_path, Notifier(None), notify=None)
    try:
        with caplog.at_level(logging.ERROR):
            assert use_case.run(now=now) == 0
    finally:
        repo.close()
    assert any("no notify section configured" in r.message for r in caplog.records)
    assert "stale_alerted" not in _ledger_run(tmp_path, "0123456789abcdef").detail


def test_failed_stale_alert_retries_next_scan(tmp_path: Path):
    now = _make_stale_run(tmp_path, hours=25)
    notifier = FlakyNotifier()
    use_case, repo = _stale_use_case(tmp_path, notifier)
    try:
        assert use_case.run(now=now) == 0  # the send failed; no flag
        assert "stale_alerted" not in _ledger_run(tmp_path, "0123456789abcdef").detail
        assert use_case.run(now=now) == 1  # next scan retries and sends
        assert len(notifier.calls) == 1
        assert notifier.attempts == 2
    finally:
        repo.close()


# -- (5) parallel multi-file uploads -------------------------------------


class BarrierTransport(LocalTransport):
    """Records which threads upload, and forces the workers to
    overlap (a Barrier only trips when all parties arrive). The
    manifest upload runs on the base instance, never a clone, so
    it does not touch the barrier."""

    def __init__(self, root, state=None):
        super().__init__(root)
        self.state = state or {"barrier": threading.Barrier(3), "threads": set(), "clones": 0}
        self._is_clone = False

    def clone(self):
        self.state["clones"] += 1
        twin = BarrierTransport(self.root, self.state)
        twin._is_clone = True
        return twin

    def upload(self, local_path, remote_dir, remote_name):
        if self._is_clone:
            self.state["threads"].add(threading.get_ident())
            self.state["barrier"].wait(timeout=10)
        return super().upload(local_path, remote_dir, remote_name)


def test_multi_file_batch_uploads_concurrently(tmp_path: Path):
    transport = BarrierTransport(tmp_path / "ftp")
    files = []
    for name in ("a.csv", "b.csv", "c.csv"):
        data = tmp_path / name
        data.write_text("x", encoding="utf-8")
        files.append((data, name))
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    transport.upload_many_with_manifest(files, manifest, "outgoing/job")
    remote = tmp_path / "ftp" / "outgoing" / "job"
    assert {p.name for p in remote.iterdir()} == {"a.csv", "b.csv", "c.csv", "r.manifest.json"}
    assert len(transport.state["threads"]) == 3  # three workers, not one
    assert transport.state["clones"] == 3


def test_single_file_upload_uses_the_same_transport(tmp_path: Path):
    transport = BarrierTransport(tmp_path / "ftp")
    data = tmp_path / "a.csv"
    data.write_text("x", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    transport.upload_many_with_manifest([(data, "a.csv")], manifest, "outgoing/job")
    assert transport.state["clones"] == 0  # no worker, no clone
    assert transport.state["threads"] == set()
    remote = tmp_path / "ftp" / "outgoing" / "job"
    assert {p.name for p in remote.iterdir()} == {"a.csv", "r.manifest.json"}


# -- (6) per-run transfer summary ----------------------------------------


def test_handoff_summary_in_log(tmp_path: Path, caplog):
    _seed_incoming(tmp_path)
    with caplog.at_level(logging.INFO):
        assert _scan_once(_watcher_cfg(tmp_path)) == {"SENT_TO_PROCESSING": 1}
    assert any(
        "SENT_TO_PROCESSING 1 file(s)," in r.message
        and "bytes in" in r.message
        and "-> FTP 2 processing/daily-report" in r.message
        for r in caplog.records
    )


def test_completion_summary_in_log_and_email(tmp_path: Path, caplog):
    _seed_incoming(tmp_path)
    cfg = _watcher_cfg(tmp_path)
    assert _scan_once(cfg) == {"SENT_TO_PROCESSING": 1}
    _processor_finish(tmp_path, "report.csv", "success")
    with caplog.at_level(logging.INFO):
        assert _scan_once(cfg) == {"COMPLETED": 1}
    assert any(
        "COMPLETED 1 file(s)," in r.message
        and "bytes in" in r.message
        and "-> FTP 1 done/daily-report" in r.message
        for r in caplog.records
    )
    assert len(FakeSMTP.sent) == 1
    body = FakeSMTP.sent[0].get_content()
    assert "COMPLETED 1 file(s)," in body
    assert "-> FTP 1 done/daily-report" in body
