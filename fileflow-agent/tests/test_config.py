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

from fileflow_agent.adapters.cli import main
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
        "ledger_db": "/tmp/agent.db",
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
    assert cfg.deliver is None


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
    cfg = _base(procedure="my-proc", procedures_module="my_procs")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert load_config(path).procedure == "my-proc"
    assert main(["--config", str(path), "--check-config"]) == 0


def test_procedures_module_missing_fails(tmp_path: Path):
    cfg = _base(procedure="my-proc", procedures_module="no_such_module_xyz")
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ModuleNotFoundError):
        load_config(path)


def test_pickup_only_config_parses_without_source():
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
    # empty config reports only that -- not staging_dir, ledger_db
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


def test_old_deliver_field_key_rejected():
    with pytest.raises(ConfigError, match="unknown key 'field'"):
        parse_config(_base(deliver={"upload_url": "https://example.com/upload", "field": "file"}))


def test_destination_directory_token_fails():
    cfg = _base(destination={"transport": "local", "root": "/tmp", "directory": "inbox/{nope}"})
    with pytest.raises(ConfigError, match="unknown token 'nope'"):
        parse_config(cfg)


def test_local_path_templates_validate():
    # staging_dir, ledger_db and log_file are templates too: they
    # render against the job's variables at use time, per run, so an
    # unknown token fails at startup -- and a job variable parses.
    cfg = parse_config(
        _base(
            variables={"client": "acme"},
            staging_dir="/tmp/staging/{client}",
            ledger_db="/tmp/{client}.db",
            log_file="/tmp/{client}.log",
        )
    )
    assert cfg.staging_dir == "/tmp/staging/{client}"
    assert cfg.ledger_db == "/tmp/{client}.db"
    assert cfg.log_file == "/tmp/{client}.log"
    for field, value in (
        ("staging_dir", "/tmp/staging/{nope}"),
        ("ledger_db", "/tmp/{nope}.db"),
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
        parse_config(_base(ledger_db="/tmp/{run_id}.db"))


def test_rename_pattern_token_fails():
    with pytest.raises(ConfigError, match="'rename_pattern'"):
        parse_config(_base(rename_pattern="{date}_{checksum}{ext}"))


def test_unknown_procedure():
    with pytest.raises(ConfigError, match="unknown procedure 'nope'"):
        parse_config(_base(procedure="nope"))


def test_deliver_needs_upload_url():
    with pytest.raises(ConfigError, match="missing required key 'upload_url'"):
        parse_config(_base(deliver={"form_field": "file"}))


def test_deliver_url_must_be_http():
    with pytest.raises(ConfigError, match="must start with http://"):
        parse_config(_base(deliver={"upload_url": "ftp://example.com/upload"}))


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


def test_check_config_ok_with_notify_and_log_file(tmp_path: Path):
    path = tmp_path / "agent.json"
    cfg = _base(
        notify={
            "smtp_host": "smtp.example.com",
            "from": "agent@example.com",
            "to": "ops@example.com",
        },
        log_file=str(tmp_path / "agent.log"),
    )
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 0


def test_check_config_ok(tmp_path: Path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 0


def test_check_config_bad(tmp_path: Path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_base(procedure="nope")), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 2


def test_download_without_source_fails(tmp_path: Path):
    cfg = _base()
    del cfg["source"]
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert main(["--config", str(path), "--mode", "download"]) == 2


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
