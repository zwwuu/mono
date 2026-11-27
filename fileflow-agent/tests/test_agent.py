"""Agent tests: pull stages a batch, push uploads it.

No watcher code is imported here -- the agent is tested on its own.
The FTP server is simulated with the "local" transport (plain
folders). A "full" run is pull then push, back to back.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_contract import file_entry, verify_files, write_manifest

from fileflow_agent.adapters.cli import (
    build_dry_run_use_case,
    build_pull_use_case,
    build_push_use_case,
)
from fileflow_agent.infrastructure.config import parse_config
from fileflow_agent.infrastructure.persistence import SqlAlchemyRunRepository
from fileflow_agent.infrastructure.transport import LocalTransport


def _pull_once(cfg: dict) -> dict:
    use_case, transport, repository = build_pull_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def _push_once(cfg: dict) -> dict:
    use_case, transport, repository = build_push_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def _push_with(cfg: dict, transport, *, sleep=None) -> dict:
    """Push with an injected transport (flaky/tampering fakes)."""
    parsed = parse_config(cfg)
    repo = SqlAlchemyRunRepository(parsed.db_url)
    try:
        from fileflow_agent.application.push import PushUseCase

        return PushUseCase(parsed, transport=transport, repository=repo, sleep=sleep).run()
    finally:
        repo.close()


def _dry_run_once(cfg: dict) -> dict:
    use_case, transport, repository = build_dry_run_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def _full_once(cfg: dict) -> dict:
    """Pull then push; returns the pull's result with the push's
    status, so end-to-end tests read like the old single run."""
    pulled = _pull_once(cfg)
    if pulled["status"] != "STAGED":
        return pulled
    pushed = _push_once(cfg)
    return {**pulled, "status": pushed["status"]}


def _agent_cfg(tmp_path: Path, source: Path) -> dict:
    return {
        "job": "daily-report",
        "source": {"type": "local", "path": str(source)},
        "expect_csv": True,
        "procedure": "normalize-csv",
        "rename_pattern": "{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "db_url": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
    }


def _agent_dir_cfg(tmp_path: Path, source_dir: Path) -> dict:
    cfg = _agent_cfg(tmp_path, source_dir / "placeholder.csv")
    cfg["source"] = {
        "type": "local-dir",
        "directory": str(source_dir),
        "pattern": "*.csv",
        "min_age_seconds": 0,
    }
    return cfg


def _ledger_states(tmp_path: Path) -> dict[str, str]:
    repo = SqlAlchemyRunRepository(tmp_path / "agent.db")
    try:
        rows = repo.list_by_state("STAGED") + repo.list_by_state("UPLOADED")
        return {r.run_id: r.state for r in rows}
    finally:
        repo.close()


# -- pull + push, end to end ----------------------------------------------


def test_agent_uploads_file_and_manifest(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    result = _full_once(_agent_cfg(tmp_path, source))
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
    assert _full_once(cfg)["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert len(list(incoming.glob("*.manifest.json"))) == 1
    result = _full_once(cfg)
    assert result["status"] == "SKIPPED"
    assert result["skipped"] == ["daily.csv"]
    assert len(list(incoming.glob("*.manifest.json"))) == 1  # no second upload


def test_rerun_without_ledger_reprocesses(tmp_path: Path):
    # No db_url, no ledger, no dedup: the second run pulls the
    # same file again instead of SKIPPING it.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    del cfg["db_url"]
    assert _full_once(cfg)["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert len(list(incoming.glob("*.manifest.json"))) == 1
    result = _full_once(cfg)
    assert result["status"] == "UPLOADED"
    assert result["skipped"] == []
    assert len(list(incoming.glob("*.manifest.json"))) == 2  # uploaded again
    assert not (tmp_path / "agent.db").exists()  # no ledger file ever created


def test_push_without_ledger_uploads(tmp_path: Path):
    # Push with no ledger: a staged batch uploads and verifies
    # against the manifest; nothing is recorded anywhere.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n\n2,20\n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    del cfg["db_url"]
    assert _pull_once(cfg)["status"] == "STAGED"
    assert _push_once(cfg)["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 1
    assert verify_files(incoming, json.loads(manifests[0].read_text())) == []
    assert not (tmp_path / "agent.db").exists()


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
    assert _full_once(cfg)["status"] == "UPLOADED"
    # Change one file: only it is fresh on the next run.
    (drop / "b.csv").write_text(" id , amount \n 2 , 21 \n", encoding="utf-8")
    result = _full_once(cfg)
    assert result["status"] == "UPLOADED"
    assert len(result["outputs"]) == 1
    assert sorted(result["skipped"]) == ["a.csv", "c.csv"]
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 2
    # Third run, nothing changed: skipped entirely.
    assert _full_once(cfg)["status"] == "SKIPPED"
    assert len(list(incoming.glob("*.manifest.json"))) == 2


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
    result = _full_once(_agent_dir_cfg(tmp_path, drop))
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


def test_rename_uses_job_variables(tmp_path: Path):
    # The filename render is the job's business logic: its own
    # variable lands in the output name. There is no {date} token
    # anymore -- a date in a name comes from the procedure or a
    # variable, not the framework.
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    cfg["variables"] = {"client": "acme"}
    cfg["rename_pattern"] = "{client}_{name}{ext}"
    result = _full_once(cfg)
    assert result["status"] == "UPLOADED"
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert list(incoming.glob("acme_daily.csv"))


# -- pull -------------------------------------------------------------------


def test_pull_stages_batch_and_records_staged(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    result = _pull_once(cfg)
    assert result["status"] == "STAGED"
    assert result["outputs"] == ["daily.csv"]
    # Nothing uploaded yet; the batch sits in staging with its manifest.
    assert list((tmp_path / "ftp").rglob("*")) == []
    staging = tmp_path / "staging" / result["run_id"]
    assert (staging / "daily.csv").exists()
    assert (staging / f"{result['run_id']}.manifest.json").exists()
    assert _ledger_states(tmp_path) == {result["run_id"]: "STAGED"}


def test_repull_skips_staged_files(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    assert _pull_once(cfg)["status"] == "STAGED"
    result = _pull_once(cfg)
    assert result["status"] == "SKIPPED"
    assert result["skipped"] == ["daily.csv"]
    # One staged batch, not two: the file was not pulled again.
    assert len(list((tmp_path / "staging").iterdir())) == 1


def test_pull_failed_keeps_staging_without_manifest(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text("", encoding="utf-8")  # empty: validate_input fails
    cfg = _agent_cfg(tmp_path, source)
    with pytest.raises(ValueError, match="empty"):
        _pull_once(cfg)
    staging = list((tmp_path / "staging").iterdir())
    assert len(staging) == 1
    assert list(staging[0].glob("*.manifest.json")) == []  # never sealed
    assert _ledger_states(tmp_path) == {}


# -- push -------------------------------------------------------------------


def test_push_uploads_staged_batch_and_cleans_staging(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    pulled = _pull_once(cfg)
    assert pulled["status"] == "STAGED"
    pushed = _push_once(cfg)
    assert pushed["status"] == "UPLOADED"
    assert pushed["uploaded"] == [pulled["run_id"]]
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    manifests = list(incoming.glob("*.manifest.json"))
    assert len(manifests) == 1
    assert verify_files(incoming, json.loads(manifests[0].read_text())) == []
    assert list((tmp_path / "staging").iterdir()) == []  # staging deleted
    assert _ledger_states(tmp_path) == {pulled["run_id"]: "UPLOADED"}


def test_push_with_nothing_staged_is_noop(tmp_path: Path):
    cfg = _agent_cfg(tmp_path, tmp_path / "daily.csv")
    assert _push_once(cfg) == {"status": "NOOP", "uploaded": [], "files": 0}
    # A push-only config (no source) is the same no-op.
    del cfg["source"]
    assert _push_once(cfg) == {"status": "NOOP", "uploaded": [], "files": 0}


def test_push_skips_manifest_less_folders(tmp_path: Path):
    # A pull still in flight, or a failed pull: a folder with files
    # but no manifest is not a batch.
    stray = tmp_path / "staging" / "0123456789abcdef"
    stray.mkdir(parents=True)
    (stray / "daily.csv").write_text("id,amount\n1,10\n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, tmp_path / "daily.csv")
    assert _push_once(cfg) == {"status": "NOOP", "uploaded": [], "files": 0}
    assert stray.exists()  # left alone


def test_push_skips_dry_run_folders(tmp_path: Path):
    # A leftover simulation folder is never a real batch, even with
    # a manifest in it (a crashed dry run leaves one behind).
    sim = tmp_path / "staging" / "dry-run-0123456789abcdef"
    sim.mkdir(parents=True)
    data = sim / "daily.csv"
    data.write_text("id,amount\n1,10\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "run_id": "0123456789abcdef",
        "job": "daily-report",
        "idempotency_key": "daily-report|0123456789abcdef",
        "files": [file_entry(data, "output")],
    }
    write_manifest(sim / "0123456789abcdef.manifest.json", manifest)
    cfg = _agent_cfg(tmp_path, tmp_path / "daily.csv")
    assert _push_once(cfg) == {"status": "NOOP", "uploaded": [], "files": 0}
    assert sim.exists()
    assert list((tmp_path / "ftp").rglob("*.csv")) == []


def _seed_staged_batch(tmp_path: Path, run_id: str, files: dict[str, str]) -> Path:
    """Stage a batch by hand: outputs + manifest, no ledger row."""
    batch = tmp_path / "staging" / run_id
    batch.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, content in sorted(files.items()):
        data = batch / name
        data.write_text(content, encoding="utf-8")
        entries.append(file_entry(data, "output"))
    manifest = {
        "version": 1,
        "run_id": run_id,
        "job": "daily-report",
        "idempotency_key": f"daily-report|{run_id}",
        "files": entries,
    }
    write_manifest(batch / f"{run_id}.manifest.json", manifest)
    return batch


def test_push_creates_ledger_row_when_missing(tmp_path: Path):
    # The ledger lost the row (lost/reset DB): the manifest is the
    # source of truth for what is staged, so the push still uploads
    # and records UPLOADED on the run_id.
    batch = _seed_staged_batch(tmp_path, "0123456789abcdef", {"a.csv": "id,amount\n1,10\n"})
    cfg = _agent_cfg(tmp_path, tmp_path / "daily.csv")
    assert _push_once(cfg)["status"] == "UPLOADED"
    assert not batch.exists()
    assert _ledger_states(tmp_path) == {"0123456789abcdef": "UPLOADED"}
    assert list((tmp_path / "ftp" / "incoming" / "daily-report").glob("a.csv"))


def test_push_bad_hash_batch_stays_staged_and_others_upload(tmp_path: Path):
    a = tmp_path / "a.csv"
    a.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text(" id , amount \n 2 , 20 \n", encoding="utf-8")
    cfg_a = _agent_cfg(tmp_path, a)
    cfg_b = _agent_cfg(tmp_path, b)
    first = _pull_once(cfg_a)
    second = _pull_once(cfg_b)
    assert first["status"] == "STAGED" and second["status"] == "STAGED"
    # Corrupt the first batch's staged output (same size, new bytes).
    staged_a = tmp_path / "staging" / first["run_id"] / "a.csv"
    staged_a.write_bytes(staged_a.read_bytes() + b"X")
    with pytest.raises(RuntimeError, match=first["run_id"]):
        _push_once(cfg_a)
    # The bad batch stays staged (row still STAGED); the good one went up.
    assert (tmp_path / "staging" / first["run_id"]).exists()
    assert not (tmp_path / "staging" / second["run_id"]).exists()
    assert _ledger_states(tmp_path) == {
        first["run_id"]: "STAGED",
        second["run_id"]: "UPLOADED",
    }
    incoming = tmp_path / "ftp" / "incoming" / "daily-report"
    assert list(incoming.glob("b.csv"))
    assert not list(incoming.glob("a.csv"))


class AlwaysFailingTransport(LocalTransport):
    """Every upload fails with a transient error."""

    def upload(self, local_path, remote_dir, remote_name):
        raise OSError("simulated blip")


def test_push_failure_leaves_row_staged_and_later_push_retries(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    pulled = _pull_once(cfg)
    assert pulled["status"] == "STAGED"
    with pytest.raises(RuntimeError, match=pulled["run_id"]):
        _push_with(cfg, AlwaysFailingTransport(tmp_path / "ftp"), sleep=lambda s: None)
    # Batch and row untouched: still staged, still there.
    assert (tmp_path / "staging" / pulled["run_id"]).exists()
    assert _ledger_states(tmp_path) == {pulled["run_id"]: "STAGED"}
    assert list((tmp_path / "ftp").rglob("*")) == []
    # The next push retries the same folder and succeeds.
    assert _push_once(cfg)["status"] == "UPLOADED"
    assert _ledger_states(tmp_path) == {pulled["run_id"]: "UPLOADED"}


# -- dry-run ------------------------------------------------------------------


def test_dry_run_uploads_nothing_and_discards_temp_staging(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    result = _dry_run_once(cfg)
    assert result["status"] == "DRY_RUN"
    assert result["pulled"]["status"] == "STAGED"
    assert result["pulled"]["outputs"] == ["daily.csv"]
    assert len(result["would_upload"]) == 1
    assert result["would_upload"][0][0] == result["pulled"]["run_id"]
    assert result["would_upload"][0][1] == ["daily.csv"]
    assert result["would_fail"] == []
    # No side effects: no upload, no ledger rows, no staging left.
    assert list((tmp_path / "ftp").rglob("*")) == []
    assert _ledger_states(tmp_path) == {}
    assert not (tmp_path / "staging").exists() or list((tmp_path / "staging").iterdir()) == []


def test_dry_run_reports_already_staged_batches(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    pulled = _pull_once(cfg)  # a real staged batch, still waiting
    result = _dry_run_once(cfg)
    # The pull leg finds nothing fresh (already staged); the push leg
    # still reports the real staged batch that would upload.
    assert result["pulled"]["status"] == "SKIPPED"
    assert len(result["would_upload"]) == 1
    assert result["would_upload"][0][0] == pulled["run_id"]
    assert result["would_upload"][0][1] == ["daily.csv"]
    assert list((tmp_path / "ftp").rglob("*")) == []
    assert (tmp_path / "staging" / pulled["run_id"]).exists()  # untouched


def test_dry_run_pull_failure_raises_and_leaves_nothing(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text("", encoding="utf-8")  # empty: validate_input fails
    cfg = _agent_cfg(tmp_path, source)
    with pytest.raises(ValueError, match="empty"):
        _dry_run_once(cfg)
    assert _ledger_states(tmp_path) == {}
    assert list((tmp_path / "ftp").rglob("*")) == []
    staging = tmp_path / "staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_dry_run_reports_bad_batch_without_failing(tmp_path: Path):
    source = tmp_path / "daily.csv"
    source.write_text(" id , amount \n 1 , 10.5 \n", encoding="utf-8")
    cfg = _agent_cfg(tmp_path, source)
    pulled = _pull_once(cfg)
    staged = tmp_path / "staging" / pulled["run_id"] / "daily.csv"
    staged.write_bytes(staged.read_bytes() + b"X")  # corrupt the hash
    result = _dry_run_once(cfg)
    assert result["pulled"]["status"] == "SKIPPED"  # already staged
    assert result["would_upload"] == []
    assert len(result["would_fail"]) == 1
    assert result["would_fail"][0][0] == pulled["run_id"]
    assert "verification failed" in result["would_fail"][0][1]
    # The bad batch is still staged, untouched by the dry run.
    assert (tmp_path / "staging" / pulled["run_id"]).exists()
    assert _ledger_states(tmp_path) == {pulled["run_id"]: "STAGED"}
