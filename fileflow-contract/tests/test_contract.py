"""Contract tests: the manifest format, identity, and verification."""

from __future__ import annotations

from pathlib import Path

from fileflow_contract import (
    MANIFEST_VERSION,
    Manifest,
    ManifestFile,
    check_manifest,
    file_entry,
    idempotency_key,
    new_run_id,
    read_manifest,
    sha256_file,
    verify_files,
    write_manifest,
)


def test_manifest_round_trip(tmp_path: Path):
    m = Manifest(
        version=MANIFEST_VERSION,
        run_id="0123456789abcdef",
        job="daily-report",
        idempotency_key="daily-report|0123456789abcdef",
        files=[ManifestFile(name="a.csv", role="output", size=3, sha256="abc")],
        direction="outbound",
        created_at="2026-11-18T00:00:00+00:00",
        extra={"agent_procedure": "rename-only"},
    )
    path = tmp_path / "run.manifest.json"
    write_manifest(path, m)
    assert not list(tmp_path.glob("*.part"))  # atomic: no .part left behind
    back = Manifest.from_dict(read_manifest(path))
    assert back == m
    assert back.output_file().name == "a.csv"
    assert back.extra["agent_procedure"] == "rename-only"


def test_idempotency_key_is_job_and_run_id():
    assert idempotency_key("daily-report", "0123456789abcdef") == "daily-report|0123456789abcdef"
    assert len(new_run_id()) == 16
    assert new_run_id() != new_run_id()


def test_verify_files_ok_and_corrupt(tmp_path: Path):
    data = tmp_path / "a.csv"
    data.write_text("id,amount\n1,10\n", encoding="utf-8")
    m = Manifest(
        version=MANIFEST_VERSION,
        run_id="r1",
        job="daily-report",
        idempotency_key="daily-report|r1",
        files=[ManifestFile(**file_entry(data, "output"))],
    )
    assert verify_files(tmp_path, m) == []
    assert sha256_file(data) == m.files[0].sha256
    data.write_text("id,amount\n1,11\n", encoding="utf-8")  # same size, new hash
    assert verify_files(tmp_path, m) == ["sha256 mismatch: a.csv"]


def test_check_manifest_reports_missing_and_version(tmp_path: Path):
    m = Manifest(
        version=MANIFEST_VERSION,
        run_id="r1",
        job="daily-report",
        idempotency_key="daily-report|r1",
        files=[ManifestFile(name="a.csv", role="output", size=3, sha256="abc")],
    )
    assert check_manifest(m, []) == ["missing file: a.csv"]
    assert check_manifest(m, [{"name": "a.csv", "size": 4, "sha256": "abc"}]) == [
        "size mismatch: a.csv expected 3, got 4"
    ]
    assert check_manifest({**m.to_dict(), "version": 99}, []) == [
        "unsupported manifest version: 99",
        "missing file: a.csv",
    ]
