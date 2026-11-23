"""Transfer-quality tests: hash-verified uploads, retry with
backoff, parallel multi-file uploads, per-run summaries.

Transport fakes subclass LocalTransport so the real FTP code is
never involved; the retry helper and the use cases are tested
with no-op sleeps so no test ever waits.
"""

import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_contract import file_entry, write_manifest

from fileflow_agent.application.download import DownloadAndUploadUseCase
from fileflow_agent.application.pickup import PickupUseCase
from fileflow_agent.infrastructure.config import parse_config
from fileflow_agent.infrastructure.persistence import SqlAlchemyRunRepository
from fileflow_agent.infrastructure.retry import with_retries
from fileflow_agent.infrastructure.transport import LocalTransport


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


class FakeNotifier:
    """Records notify calls; never sends mail."""

    def __init__(self):
        self.calls: list[dict] = []

    def notify(self, **kwargs):
        self.calls.append(kwargs)


class FakeSource:
    """A source that returns one fixed file (or raises)."""

    def __init__(self, path: Path, error: Exception | None = None):
        self.path = path
        self.error = error
        self.calls = 0

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        dest = Path(dest_dir) / self.path.name
        dest.write_bytes(self.path.read_bytes())
        return [dest]


def _run_download(cfg: dict, transport, source, *, sleep=None, notifier=None):
    parsed = parse_config(cfg)
    repo = SqlAlchemyRunRepository(parsed.ledger_db)
    try:
        use_case = DownloadAndUploadUseCase(
            parsed,
            source=source,
            transport=transport,
            repository=repo,
            notifier=notifier,
            sleep=sleep,
        )
        return use_case.run()
    finally:
        repo.close()


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
    data.write_text("id,amount\n1,10\n", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        transport.upload_many_with_manifest([(data, "a.csv")], manifest, "incoming/job")
    assert not (tmp_path / "ftp" / "incoming" / "job" / "r.manifest.json").exists()


def test_size_mismatch_after_upload_raises_and_no_manifest(tmp_path: Path):
    transport = TruncatingTransport(tmp_path / "ftp")
    data = tmp_path / "a.csv"
    data.write_text("id,amount\n1,10\n", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError, match="size mismatch"):
        transport.upload_many_with_manifest([(data, "a.csv")], manifest, "incoming/job")
    assert not (tmp_path / "ftp" / "incoming" / "job" / "r.manifest.json").exists()


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


class FlakyTransport(LocalTransport):
    """Fails the first N uploads with OSError, then works."""

    def __init__(self, root, fail_times: int = 2):
        super().__init__(root)
        self.fail_times = fail_times
        self.upload_calls = 0

    def upload(self, local_path, remote_dir, remote_name):
        self.upload_calls += 1
        if self.upload_calls <= self.fail_times:
            raise OSError("simulated blip")
        return super().upload(local_path, remote_dir, remote_name)


def test_download_run_retries_transient_upload_failure(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    transport = FlakyTransport(tmp_path / "ftp", fail_times=2)
    sleeps: list[float] = []
    result = _run_download(
        cfg,
        transport,
        FakeSource(source),
        sleep=sleeps.append,
    )
    assert result["status"] == "UPLOADED"
    assert sleeps == [1.0, 2.0]
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert len(list(incoming.glob("*.manifest.json"))) == 1


def test_download_run_fails_after_exhausting_retries(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    transport = FlakyTransport(tmp_path / "ftp", fail_times=99)
    sleeps: list[float] = []
    with pytest.raises(OSError, match="simulated blip"):
        _run_download(cfg, transport, FakeSource(source), sleep=sleeps.append)
    assert transport.upload_calls == 3  # 3 attempts, then give up
    assert sleeps == [1.0, 2.0]


def test_download_run_does_not_retry_validation_errors(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    fake_source = FakeSource(source, error=ValueError("bad input"))
    with pytest.raises(ValueError, match="bad input"):
        _run_download(cfg, LocalTransport(tmp_path / "ftp"), fake_source, sleep=lambda s: None)
    assert fake_source.calls == 1


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
    transport.upload_many_with_manifest(files, manifest, "incoming/job")
    remote = tmp_path / "ftp" / "incoming" / "job"
    assert {p.name for p in remote.iterdir()} == {"a.csv", "b.csv", "c.csv", "r.manifest.json"}
    assert len(transport.state["threads"]) == 3  # three workers, not one
    assert transport.state["clones"] == 3


def test_single_file_upload_uses_the_same_transport(tmp_path: Path):
    transport = BarrierTransport(tmp_path / "ftp")
    data = tmp_path / "a.csv"
    data.write_text("x", encoding="utf-8")
    manifest = tmp_path / "r.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    transport.upload_many_with_manifest([(data, "a.csv")], manifest, "incoming/job")
    assert transport.state["clones"] == 0  # no worker, no clone
    assert transport.state["threads"] == set()
    remote = tmp_path / "ftp" / "incoming" / "job"
    assert {p.name for p in remote.iterdir()} == {"a.csv", "r.manifest.json"}


# -- (6) per-run transfer summary ----------------------------------------


def test_download_summary_in_log_and_email(tmp_path: Path, caplog):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    notifier = FakeNotifier()
    with caplog.at_level(logging.INFO):
        result = _run_download(
            cfg, LocalTransport(tmp_path / "ftp"), FakeSource(source), notifier=notifier
        )
    assert result["status"] == "UPLOADED"
    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["status"] == "UPLOADED"
    assert call["summary"] is not None
    assert "1 file(s)," in call["summary"]
    assert "bytes in" in call["summary"]
    assert "-> incoming/daily-report" in call["summary"]
    assert any(call["summary"] in record.message for record in caplog.records)


class FakeUploader:
    """Records website uploads; never opens a socket."""

    def __init__(self):
        self.uploaded: list[tuple[str, list[str]]] = []

    def upload_many(self, file_paths, *, run_id, job):
        self.uploaded.append((run_id, [p.name for p in file_paths]))


def _seed_outgoing(tmp_path: Path) -> None:
    out_dir = tmp_path / "ftp" / "outgoing" / "daily-report"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = out_dir / "to-upload.csv"
    data.write_text("id,amount\n7,99\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "run_id": "0123456789abcdef",
        "job": "daily-report",
        "direction": "outbound",
        "idempotency_key": "daily-report|0123456789abcdef",
        "files": [file_entry(data, "output")],
    }
    write_manifest(out_dir / "to-upload.manifest.json", manifest)


def _pickup_cfg(tmp_path: Path) -> dict:
    return {
        "job": "daily-report",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
        "deliver": {"upload_url": "http://127.0.0.1:9/upload", "form_field": "file"},
    }


def test_pickup_summary_in_log_and_email(tmp_path: Path, caplog):
    _seed_outgoing(tmp_path)
    cfg = parse_config(_pickup_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(cfg.ledger_db)
    uploader = FakeUploader()
    notifier = FakeNotifier()
    try:
        use_case = PickupUseCase(
            cfg,
            transport=LocalTransport(tmp_path / "ftp"),
            repository=repo,
            uploader=uploader,
            notifier=notifier,
            sleep=lambda s: None,
        )
        with caplog.at_level(logging.INFO):
            assert use_case.run() == {"UPLOADED_TO_WEBSITE": 1}
    finally:
        repo.close()
    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["status"] == "UPLOADED_TO_WEBSITE"
    assert "1 file(s)," in call["summary"]
    assert "bytes in" in call["summary"]
    assert "-> http://127.0.0.1:9/upload" in call["summary"]
    assert any(call["summary"] in record.message for record in caplog.records)


def test_pickup_run_retries_transient_website_failure(tmp_path: Path):
    _seed_outgoing(tmp_path)
    cfg = parse_config(_pickup_cfg(tmp_path))
    repo = SqlAlchemyRunRepository(cfg.ledger_db)

    class FlakyUploader(FakeUploader):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def upload_many(self, file_paths, *, run_id, job):
            self.calls += 1
            if self.calls <= 2:
                raise OSError("website blip")
            return super().upload_many(file_paths, run_id=run_id, job=job)

    uploader = FlakyUploader()
    sleeps: list[float] = []
    try:
        use_case = PickupUseCase(
            cfg,
            transport=LocalTransport(tmp_path / "ftp"),
            repository=repo,
            uploader=uploader,
            notifier=FakeNotifier(),
            sleep=sleeps.append,
        )
        assert use_case.run() == {"UPLOADED_TO_WEBSITE": 1}
    finally:
        repo.close()
    assert uploader.calls == 3
    assert sleeps == [1.0, 2.0]
