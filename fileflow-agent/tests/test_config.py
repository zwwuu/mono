"""Config parser tests: fail fast -- one error, the first problem; valid configs parse.

The parser is the single source of truth for the config shape, so
the shipped example configs are parsed here too -- a bad example
fails the suite, not a user's first run.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_contract import (
    MANIFEST_VERSION,
    Manifest,
    ManifestFile,
    file_entry,
    read_manifest,
    write_manifest,
)

from fileflow_agent.adapters.cli import dry_run_checks, main
from fileflow_agent.infrastructure.config import (
    ConfigError,
    load_config,
    parse_config,
    parse_source,
    parse_transport,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "config"


def _base(**overrides):
    cfg = {
        "job": "ftp-inbox",
        "category": "sales",
        "source": {"type": "local", "path": "/tmp/in.csv"},
        "staging_dir": "/tmp/staging",
        "db_url": "/tmp/agent.db",
        "destination": {"transport": "local", "root": "/tmp/dest"},
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_parses_with_defaults():
    cfg = parse_config(_base())
    assert cfg.job == "ftp-inbox"
    assert cfg.category == "sales"
    assert cfg.procedure == "rename-only"
    assert cfg.expect_csv is False
    assert cfg.rename_pattern == "{name}{ext}"
    assert cfg.source.type == "local"
    assert cfg.source.path == "/tmp/in.csv"
    assert cfg.destination.transport.kind == "local"
    assert cfg.destination.directory is None


def test_config_without_db_url_parses():
    # The ledger is optional: omit db_url and the config parses
    # with db_url None.
    cfg = parse_config({k: v for k, v in _base().items() if k != "db_url"})
    assert cfg.db_url is None


def test_old_ledger_db_key_fails_loud():
    # The rename is outright: the old key is unknown now.
    with pytest.raises(ConfigError, match="unknown key 'ledger_db'"):
        parse_config(_base(ledger_db="/tmp/agent.db"))


def test_per_type_defaults():
    assert parse_source({"type": "local-dir", "directory": "/tmp"}) == parse_source(
        {"type": "local-dir", "directory": "/tmp", "pattern": "*", "min_age_seconds": 2}
    )
    remote = parse_source(
        {"type": "remote-dir", "transport": "ftp", "host": "h", "remote_dir": "."}
    )
    assert remote.min_age_seconds == 5
    assert remote.transport.port == 21
    sftp = parse_transport({"transport": "sftp", "host": "h"})
    assert sftp.port == 22
    assert sftp.username == ""


def test_playwright_source_parses():
    source = parse_source(
        {
            "type": "playwright",
            "actions": [
                {"action": "goto", "url": "https://portal.example.com/login"},
                {"action": "download", "selector": "a.download"},
            ],
            "executable_path": "/usr/bin/chromium",
        }
    )
    assert source.type == "playwright"
    assert source.headless is True
    assert source.timeout_seconds == 60
    assert source.executable_path == "/usr/bin/chromium"
    assert len(source.actions) == 2


def test_remote_files_optional_parses():
    source = parse_source(
        {
            "type": "remote-files",
            "transport": "local",
            "root": "/tmp",
            "files": [
                {"directory": "inbox", "name": "req.csv"},
                {"directory": "inbox", "name": "opt.csv", "optional": True},
            ],
        }
    )
    assert source.files[0].optional is False
    assert source.files[1].optional is True


def test_procedures_module_loads_custom_procedure(tmp_path: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "my_procs.py").write_text(
        "from pathlib import Path\n"
        "from fileflow_agent.domain.procedures import register_procedure\n"
        "\n"
        "\n"
        "def my_proc(input_path: Path, workdir: Path, ctx: dict) -> Path:\n"
        "    return workdir / input_path.name\n"
        "\n"
        "\n"
        "register_procedure('my-proc', '1.0', my_proc)\n",
        encoding="utf-8",
    )
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    cfg = _validate_cfg(tmp_path, source=src, procedure="my-proc", procedures_module="my_procs")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert load_config(path).procedure == "my-proc"
    assert main(["--config", str(path), "--action", "validate"]) == 0


def test_procedures_module_missing_fails(tmp_path: Path):
    cfg = _base(procedure="my-proc", procedures_module="no_such_module_xyz")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ModuleNotFoundError):
        load_config(path)


def test_push_only_config_parses_without_source():
    cfg = _base()
    del cfg["source"]
    parsed = parse_config(cfg)
    assert parsed.source is None


def test_all_example_configs_parse():
    examples = sorted(EXAMPLES.glob("*.example.json"))
    assert examples, "no example configs found"
    for path in examples:
        parse_config(json.loads(path.read_text(encoding="utf-8")))


def test_missing_required_keys_reports_first_only():
    # Check order: 'job' is the first required key checked, so an
    # empty config reports only that -- not staging_dir, db_url
    # or the missing destination.
    with pytest.raises(ConfigError) as exc:
        parse_config({})
    assert str(exc.value) == "config: missing required key 'job'"


def test_unknown_top_level_key():
    with pytest.raises(ConfigError, match="unknown key 'typo'"):
        parse_config(_base(typo=1))


def test_unknown_source_type():
    with pytest.raises(ConfigError, match="unknown source type 'carrier-pigeon'"):
        parse_source({"type": "carrier-pigeon"})


def test_unknown_transport():
    with pytest.raises(ConfigError, match="unknown transport 'teleport'"):
        parse_transport({"transport": "teleport", "host": "h"})


@pytest.mark.parametrize("port", [0, 70000, "22"])
def test_bad_port(port):
    with pytest.raises(ConfigError, match="'port'"):
        parse_config(_base(destination={"transport": "ftp", "host": "h", "port": port}))


def test_remote_files_empty_list():
    with pytest.raises(ConfigError, match="must be a non-empty list"):
        parse_source({"type": "remote-files", "transport": "local", "root": "/tmp", "files": []})


def test_remote_files_name_must_not_be_a_path():
    with pytest.raises(ConfigError, match="path separator"):
        parse_source(
            {
                "type": "remote-files",
                "transport": "local",
                "root": "/tmp",
                "files": [{"directory": "inbox", "name": "a/b.csv"}],
            }
        )


def test_unknown_template_token_fails():
    cfg = _base(
        source={
            "type": "remote-files",
            "transport": "local",
            "root": "/tmp",
            "files": [{"directory": "inbox", "name": "Test-{nope}.csv"}],
        }
    )
    with pytest.raises(ConfigError, match="unknown token 'nope'"):
        parse_config(cfg)


def test_builtin_tokens_are_gone():
    # All seven built-ins are gone: {year} {month} {day} {date}
    # {time} {job} {category} are unknown tokens now, even with a
    # category set and even in a rename pattern.
    for token in ("year", "month", "day", "date", "time", "job", "category"):
        cfg = _base(
            source={
                "type": "remote-files",
                "transport": "local",
                "root": "/tmp",
                "files": [{"directory": f"inbox/{{{token}}}", "name": "Test.csv"}],
            }
        )
        with pytest.raises(ConfigError, match=f"unknown token '{token}'"):
            parse_config(cfg)
    with pytest.raises(ConfigError, match="unknown token 'date'"):
        parse_config(_base(rename_pattern="{date}_{name}{ext}"))


def test_old_kind_key_rejected():
    with pytest.raises(ConfigError, match="unknown key 'kind'"):
        parse_config(_base(kind="sales"))


def test_old_kind_token_rejected():
    cfg = _base(
        source={
            "type": "remote-files",
            "transport": "local",
            "root": "/tmp",
            "files": [{"directory": "inbox/{kind}", "name": "Test3-{kind}.csv"}],
        }
    )
    with pytest.raises(ConfigError, match="unknown token 'kind'"):
        parse_config(cfg)


def test_old_local_dir_key_rejected():
    with pytest.raises(ConfigError, match="unknown key 'dir'"):
        parse_source({"type": "local-dir", "dir": "/tmp"})


def test_old_chrome_binary_key_rejected():
    with pytest.raises(ConfigError, match="unknown key 'chrome_binary'"):
        parse_source(
            {
                "type": "selenium",
                "actions": [{"action": "goto", "url": "https://example.com"}],
                "chrome_binary": "/usr/bin/google-chrome",
            }
        )


def test_deliver_key_rejected():
    # The reverse flow is gone: a config naming "deliver" fails
    # loud as an unknown key.
    with pytest.raises(ConfigError, match="unknown key 'deliver'"):
        parse_config(
            _base(deliver={"upload_url": "https://example.com/upload", "form_field": "file"})
        )


def test_destination_directory_token_fails():
    cfg = _base(destination={"transport": "local", "root": "/tmp", "directory": "inbox/{nope}"})
    with pytest.raises(ConfigError, match="unknown token 'nope'"):
        parse_config(cfg)


def test_local_path_templates_validate():
    # staging_dir, db_url and log_file are templates too: they
    # render against the job's variables at use time, per run, so an
    # unknown token fails at startup -- and a job variable parses.
    cfg = parse_config(
        _base(
            variables={"client": "acme"},
            staging_dir="/tmp/staging/{client}",
            db_url="/tmp/{client}.db",
            log_file="/tmp/{client}.log",
        )
    )
    assert cfg.staging_dir == "/tmp/staging/{client}"
    assert cfg.db_url == "/tmp/{client}.db"
    assert cfg.log_file == "/tmp/{client}.log"
    for field, value in (
        ("staging_dir", "/tmp/staging/{nope}"),
        ("db_url", "/tmp/{nope}.db"),
        ("log_file", "/tmp/{nope}.log"),
    ):
        with pytest.raises(ConfigError, match="unknown token 'nope'"):
            parse_config(_base(**{field: value}))


def test_local_path_templates_have_no_per_file_tokens():
    # {name}/{ext}/{run_id} exist only for the rename pattern:
    # staging_dir renders with no file in hand, so they fail here
    # rather than at the first run.
    with pytest.raises(ConfigError, match="unknown token 'name'"):
        parse_config(_base(staging_dir="/tmp/staging/{name}"))
    with pytest.raises(ConfigError, match="unknown token 'run_id'"):
        parse_config(_base(db_url="/tmp/{run_id}.db"))


def test_rename_pattern_token_fails():
    with pytest.raises(ConfigError, match="'rename_pattern'"):
        parse_config(_base(rename_pattern="{date}_{checksum}{ext}"))


def test_unknown_procedure():
    with pytest.raises(ConfigError, match="unknown procedure 'nope'"):
        parse_config(_base(procedure="nope"))


@pytest.mark.parametrize(
    "override, field",
    [
        ({"job": 123}, "'job'"),
        ({"expect_csv": "yes"}, "'expect_csv'"),
        (
            {"source": {"type": "local-dir", "directory": "/tmp", "min_age_seconds": -1}},
            "'min_age_seconds'",
        ),
        (
            {"source": {"type": "website", "url": "https://x", "timeout_seconds": 0}},
            "'timeout_seconds'",
        ),
        (
            {
                "source": {
                    "type": "selenium",
                    "actions": [{"action": "download", "selector": "a"}],
                    "headless": "yes",
                }
            },
            "'headless'",
        ),
        (
            {
                "source": {
                    "type": "playwright",
                    "actions": [{"action": "download", "selector": "a"}],
                    "executable_path": 123,
                }
            },
            "'executable_path'",
        ),
        (
            {
                "source": {
                    "type": "remote-files",
                    "transport": "local",
                    "root": "/tmp",
                    "files": [{"directory": "inbox", "name": "a.csv", "optional": "yes"}],
                }
            },
            "'optional'",
        ),
    ],
)
def test_wrong_types_and_values(override, field):
    with pytest.raises(ConfigError, match=field):
        parse_config(_base(**override))


def test_multiple_problems_reports_first_only():
    # Three problems, one error: the missing 'job' is checked before
    # the unknown procedure and before the destination's bad port.
    cfg = _base(procedure="nope", destination={"transport": "ftp", "host": "h", "port": 70000})
    del cfg["job"]
    with pytest.raises(ConfigError) as exc:
        parse_config(cfg)
    assert str(exc.value) == "config: missing required key 'job'"


def test_notify_parses_with_defaults():
    cfg = parse_config(
        _base(
            notify={
                "smtp_host": "smtp.example.com",
                "from": "agent@example.com",
                "to": "ops@example.com",
                "password_env": "FILEFLOW_SMTP_PASSWORD",
            }
        )
    )
    assert cfg.notify is not None
    assert cfg.notify.smtp_host == "smtp.example.com"
    assert cfg.notify.smtp_port == 587
    assert cfg.notify.from_addr == "agent@example.com"
    assert cfg.notify.to == "ops@example.com"
    assert cfg.notify.password_env == "FILEFLOW_SMTP_PASSWORD"


def test_no_notify_section_is_none():
    assert parse_config(_base()).notify is None


def test_notify_missing_fields_reported():
    with pytest.raises(ConfigError, match="missing required key 'to'"):
        parse_config(_base(notify={"smtp_host": "smtp.example.com", "from": "a@b.c"}))


def test_notify_bad_port():
    with pytest.raises(ConfigError, match="'smtp_port' must be <= 65535"):
        parse_config(
            _base(
                notify={
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 70000,
                    "from": "a@b.c",
                    "to": "o@b.c",
                }
            )
        )


def test_notify_unknown_key():
    with pytest.raises(ConfigError, match="unknown key 'server'"):
        parse_config(
            _base(
                notify={
                    "smtp_host": "smtp.example.com",
                    "from": "a@b.c",
                    "to": "o@b.c",
                    "server": "smtp.example.com",
                }
            )
        )


def test_log_file_parses():
    assert parse_config(_base(log_file="/var/log/agent.log")).log_file == "/var/log/agent.log"
    assert parse_config(_base()).log_file is None


def _validate_cfg(tmp_path: Path, *, source: Path | None = None, **overrides) -> dict:
    """A config whose paths all live under tmp_path."""
    src = source or tmp_path / "in.csv"
    cfg = _base(
        source={"type": "local", "path": str(src)},
        staging_dir=str(tmp_path / "staging"),
        db_url=str(tmp_path / "agent.db"),
        destination={"transport": "local", "root": str(tmp_path / "dest")},
    )
    cfg.update(overrides)
    return cfg


def _validate_cfg_path(tmp_path: Path, staging: Path, **overrides) -> Path:
    """Write a validate config pointing at the given staging dir."""
    path = tmp_path / "agent.json"
    path.write_text(
        json.dumps(_validate_cfg(tmp_path, staging_dir=str(staging), **overrides)),
        encoding="utf-8",
    )
    return path


def _staged_batch(staging: Path, run_id: str, job: str, files: dict[str, bytes]) -> Path:
    """A staged batch built with the contract's manifest writer:
    data files plus a sealed <run_id>.manifest.json."""
    batch = staging / run_id
    batch.mkdir(parents=True)
    entries = []
    for name, data in files.items():
        p = batch / name
        p.write_bytes(data)
        entries.append(ManifestFile(**file_entry(p, "output")))
    manifest = Manifest(
        version=MANIFEST_VERSION,
        run_id=run_id,
        job=job,
        idempotency_key=f"{job}|{run_id}",
        files=entries,
    )
    write_manifest(batch / f"{run_id}.manifest.json", manifest)
    return batch


def test_validate_ok_valid_batch(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 0
    out = capsys.readouterr().out
    assert "config OK: job 'ftp-inbox'" in out
    assert f"validate: batch {'a' * 16}: OK" in out
    assert "validate: OK (1 batch(es) valid)" in out


def test_validate_hash_mismatch(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    batch = _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    (batch / "daily.csv").write_bytes(b"id,amount\n1,10\nX")  # same name, new hash
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "sha256 mismatch" in capsys.readouterr().out


def test_validate_size_mismatch(tmp_path: Path, capsys):
    # The manifest records the wrong size but the right hash: only
    # the size check fires.
    staging = tmp_path / "staging"
    batch = _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    manifest_path = batch / f"{'a' * 16}.manifest.json"
    manifest = Manifest.from_dict(read_manifest(manifest_path))
    manifest.files = [
        ManifestFile(name=f.name, role=f.role, size=f.size + 1, sha256=f.sha256)
        for f in manifest.files
    ]
    write_manifest(manifest_path, manifest)
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "size mismatch" in capsys.readouterr().out


def test_validate_missing_file(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    batch = _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    (batch / "daily.csv").unlink()
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "missing file" in capsys.readouterr().out


def test_validate_malformed_manifest(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    batch = _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    (batch / f"{'a' * 16}.manifest.json").write_text("not json{", encoding="utf-8")
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "malformed manifest" in capsys.readouterr().out


def test_validate_version_mismatch(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    batch = _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    manifest_path = batch / f"{'a' * 16}.manifest.json"
    manifest = Manifest.from_dict(read_manifest(manifest_path))
    manifest.version = MANIFEST_VERSION + 1
    write_manifest(manifest_path, manifest)
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "version mismatch" in capsys.readouterr().out


def test_validate_run_id_mismatch(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    _staged_batch(staging, "a" * 16, "ftp-inbox", {"daily.csv": b"id,amount\n1,10\n"})
    # The manifest still says aaaa..., the folder is now bbbb...
    (staging / ("a" * 16)).rename(staging / ("b" * 16))
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "run_id mismatch" in capsys.readouterr().out


def test_validate_manifestless_folder_fails(tmp_path: Path, capsys):
    folder = tmp_path / "staging" / ("c" * 16)
    folder.mkdir(parents=True)
    (folder / "daily.csv").write_text("id,amount\n1,10\n", encoding="utf-8")
    path = _validate_cfg_path(tmp_path, tmp_path / "staging")
    assert main(["--config", str(path), "--action", "validate"]) == 1
    assert "INCOMPLETE (no manifest)" in capsys.readouterr().out


def test_validate_skips_dry_run_folders(tmp_path: Path, capsys):
    folder = tmp_path / "staging" / "dry-run-abc123"
    folder.mkdir(parents=True)
    (folder / "garbage.json").write_text("not a manifest", encoding="utf-8")
    path = _validate_cfg_path(tmp_path, tmp_path / "staging")
    assert main(["--config", str(path), "--action", "validate"]) == 0
    out = capsys.readouterr().out
    assert "skipped (simulation folder)" in out
    assert "validate: OK" in out


def test_validate_skips_other_job_batches(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    _staged_batch(staging, "a" * 16, "other-job", {"daily.csv": b"id,amount\n1,10\n"})
    path = _validate_cfg_path(tmp_path, staging)
    assert main(["--config", str(path), "--action", "validate"]) == 0
    assert "skipped (other job 'other-job')" in capsys.readouterr().out


def test_validate_empty_staging(tmp_path: Path, capsys):
    # The staging dir does not exist at all.
    path = _validate_cfg_path(tmp_path, tmp_path / "staging")
    assert main(["--config", str(path), "--action", "validate"]) == 0
    assert "nothing staged to validate" in capsys.readouterr().out


def test_validate_bad_config(tmp_path: Path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_base(procedure="nope")), encoding="utf-8")
    assert main(["--config", str(path), "--action", "validate"]) == 2


def test_validate_does_not_touch_the_ledger(tmp_path: Path):
    # A full run first, so the ledger exists with one run; validate
    # never opens the ledger, so not a byte changes.
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_validate_cfg(tmp_path, source=src)), encoding="utf-8")
    assert main(["--config", str(path), "--action", "full"]) == 0
    ledger = tmp_path / "agent.db"
    before = ledger.read_bytes()
    assert main(["--config", str(path), "--action", "validate"]) == 0
    assert ledger.read_bytes() == before


def test_pull_without_source_fails(tmp_path: Path):
    cfg = _base()
    del cfg["source"]
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert main(["--config", str(path), "--action", "pull"]) == 2
    assert main(["--config", str(path), "--action", "full"]) == 2
    assert main(["--config", str(path), "--action", "dry-run"]) == 2


def test_push_without_source_is_noop(tmp_path: Path):
    cfg = _base()
    del cfg["source"]
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert main(["--config", str(path), "--action", "push"]) == 0


def test_old_flags_rejected(tmp_path: Path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_base()), encoding="utf-8")
    for argv in (
        ["--config", str(path), "--mode", "download"],
        ["--config", str(path), "--check-config"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2


def test_dry_run_action(tmp_path: Path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    # The destination directory is pre-created here; the
    # missing-directory case is covered separately.
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_validate_cfg(tmp_path, source=src)), encoding="utf-8")
    assert main(["--config", str(path), "--action", "dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run check: source: OK" in out
    assert "dry run check: staging: OK" in out
    assert "dry run check: ledger: OK" in out
    assert "dry run check: destination: OK" in out
    assert "dry run: pull would stage 1 file(s)" in out
    assert "dry run: push would upload batch" in out
    assert "no uploads, no ledger writes" in out
    # No side effects: nothing uploaded, no ledger file created.
    assert [p for p in (tmp_path / "dest").rglob("*") if p.is_file()] == []
    assert not (tmp_path / "agent.db").exists()


def test_dry_run_action_without_db_url(tmp_path: Path, capsys):
    # No db_url: the ledger check passes with a note, the pull leg
    # still simulates, and no ledger file is created.
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    cfg = _validate_cfg(tmp_path, source=src)
    del cfg["db_url"]
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert main(["--config", str(path), "--action", "dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run check: ledger: OK -- not configured" in out
    assert "dry run: pull would stage 1 file(s)" in out
    assert "dry run: push would upload batch" in out
    assert not (tmp_path / "agent.db").exists()


def test_dry_run_fails_when_staging_not_writable(tmp_path: Path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    # A regular file where the staging directory should be: the
    # check cannot create it, so the run stops before the pull leg.
    (tmp_path / "staging").write_text("not a directory", encoding="utf-8")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_validate_cfg(tmp_path, source=src)), encoding="utf-8")
    assert main(["--config", str(path), "--action", "dry-run"]) == 1
    captured = capsys.readouterr()
    assert "dry run check: staging: FAILED" in captured.out
    assert "dry run: 1 check(s) failed: staging" in captured.err
    # The pull leg never ran: no ledger file, nothing uploaded.
    assert not (tmp_path / "agent.db").exists()
    assert [p for p in (tmp_path / "dest").rglob("*") if p.is_file()] == []


def test_dry_run_passes_when_local_destination_missing(tmp_path: Path, capsys):
    # A local destination directory that does not exist yet: the
    # real push creates it on upload, so the dry-run check must
    # pass too -- it used to fail the run before the pull leg.
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_validate_cfg(tmp_path, source=src)), encoding="utf-8")
    assert main(["--config", str(path), "--action", "dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run check: destination: OK" in out
    assert "did not exist, created by probe" in out
    assert "dry run: pull would stage 1 file(s)" in out
    # The probe created the directory but left no file behind,
    # and nothing was uploaded; no ledger file either.
    dest = tmp_path / "dest"
    assert [p for p in dest.rglob("*") if p.is_file()] == []
    assert not list(dest.rglob(".fileflow-write-probe-*"))
    assert not (tmp_path / "agent.db").exists()


def test_dry_run_probe_leaves_no_trace(tmp_path: Path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_validate_cfg(tmp_path, source=src)), encoding="utf-8")
    assert main(["--config", str(path), "--action", "dry-run"]) == 0
    out = capsys.readouterr().out
    assert "write probe uploaded and removed" in out
    dest = tmp_path / "dest"
    assert [p for p in dest.rglob("*") if p.is_file()] == []
    assert not list(dest.rglob(".fileflow-write-probe-*"))


class _FtpStyleTransport:
    """Upload fails when the directory does not exist, like real
    FTP/SFTP -- no mkdir on upload."""

    kind = "ftp"

    def list_names(self, remote_dir):
        return []

    def upload(self, local_path, remote_dir, remote_name):
        raise OSError("No such file or directory")

    def delete(self, remote_dir, remote_name):
        pass

    def close(self):
        pass


class _FailingUploadTransport:
    """List works, upload always fails."""

    kind = "local"

    def list_names(self, remote_dir):
        return []

    def upload(self, local_path, remote_dir, remote_name):
        raise OSError("permission denied")

    def delete(self, remote_dir, remote_name):
        pass

    def close(self):
        pass


class _LeakyDeleteTransport:
    """Upload records the name; delete never removes it."""

    kind = "local"

    def __init__(self):
        self.names = []

    def list_names(self, remote_dir):
        return list(self.names)

    def upload(self, local_path, remote_dir, remote_name):
        self.names.append(remote_name)

    def delete(self, remote_dir, remote_name):
        pass

    def close(self):
        pass


def test_dry_run_checks_probe_upload_failure(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    cfg = parse_config(_validate_cfg(tmp_path, source=src))
    results = dry_run_checks(cfg, destination_transport=_FailingUploadTransport())
    by_label = {r.label: r for r in results}
    assert by_label["source"].ok
    assert by_label["staging"].ok
    assert by_label["ledger"].ok
    assert not by_label["destination"].ok
    assert "write probe failed" in by_label["destination"].detail


def test_dry_run_checks_probe_delete_warning(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    (tmp_path / "dest" / "incoming" / "ftp-inbox").mkdir(parents=True)
    cfg = parse_config(_validate_cfg(tmp_path, source=src))
    results = dry_run_checks(cfg, destination_transport=_LeakyDeleteTransport())
    by_label = {r.label: r for r in results}
    dest = by_label["destination"]
    assert dest.ok  # the write itself worked
    assert len(dest.warnings) == 1
    assert "probe file left" in dest.warnings[0]


def test_dry_run_checks_destination_missing_fails_for_ftp(tmp_path: Path):
    # The split: a missing directory fails for a transport that
    # does not create directories (FTP/SFTP upload would fail the
    # same way a real push does) -- local passes instead, because
    # its upload mkdirs the directory.
    src = tmp_path / "in.csv"
    src.write_text("id,amount\n1,10\n", encoding="utf-8")
    cfg = parse_config(_validate_cfg(tmp_path, source=src))
    results = dry_run_checks(cfg, destination_transport=_FtpStyleTransport())
    by_label = {r.label: r for r in results}
    assert by_label["source"].ok
    assert by_label["staging"].ok
    assert by_label["ledger"].ok
    assert not by_label["destination"].ok
    assert "write probe failed" in by_label["destination"].detail


def test_job_variables_are_the_template_tokens():
    # The token set is the job's business logic: a template using
    # the job's own variable parses, and renders with its value.
    # There are no built-ins left: {date} fails even though the
    # job used to get one for free.
    from fileflow_agent.domain.integrity import render_template

    cfg = parse_config(
        _base(
            variables={"client": "acme"},
            source={
                "type": "remote-files",
                "transport": "local",
                "root": "/tmp",
                "files": [{"directory": "inbox/{client}", "name": "Test-{client}.csv"}],
            },
        )
    )
    assert cfg.variables == {"client": "acme"}
    assert render_template("inbox/{client}", dict(cfg.variables)) == "inbox/acme"
    with pytest.raises(ConfigError, match="unknown token 'date'"):
        parse_config(
            _base(
                variables={"client": "acme"},
                source={
                    "type": "remote-files",
                    "transport": "local",
                    "root": "/tmp",
                    "files": [{"directory": "inbox/{client}", "name": "Test-{date}.csv"}],
                },
            )
        )


def test_rename_pattern_uses_job_variables():
    from fileflow_agent.domain.integrity import render_name

    cfg = parse_config(
        _base(
            variables={"client": "acme"},
            rename_pattern="{client}_{name}{ext}",
        )
    )
    assert (
        render_name(
            cfg.rename_pattern,
            name="daily",
            ext=".csv",
            run_id="0" * 16,
            variables=cfg.variables,
        )
        == "acme_daily.csv"
    )


@pytest.mark.parametrize(
    "variables, problem",
    [
        ({"name": "x"}, "collides with per-render token 'name'"),
        ({"ext": "x"}, "collides with per-render token 'ext'"),
        ({"run_id": "x"}, "collides with per-render token 'run_id'"),
        ({"my-var": "x"}, "variable names must be identifiers"),
        ({"client": 3}, "variable 'client' must be a string"),
    ],
)
def test_job_variables_rejected(variables, problem):
    with pytest.raises(ConfigError, match=problem):
        parse_config(_base(variables=variables))


def test_date_format_and_time_format_keys_rejected():
    # They formatted {date}/{time}; with the built-ins gone they
    # format nothing, so the keys are removed, not kept.
    with pytest.raises(ConfigError, match="unknown key 'date_format'"):
        parse_config(_base(date_format="%Y-%m-%d"))
    with pytest.raises(ConfigError, match="unknown key 'time_format'"):
        parse_config(_base(time_format="%H:%M"))


def test_unknown_token_still_fails_with_variables_present():
    cfg = _base(
        variables={"client": "acme"},
        source={
            "type": "remote-files",
            "transport": "local",
            "root": "/tmp",
            "files": [{"directory": "inbox", "name": "Test-{nope}.csv"}],
        },
    )
    with pytest.raises(ConfigError, match="unknown token 'nope'"):
        parse_config(cfg)
