"""Adapters -- CLI and composition root (agent).

This is the only place that knows both the use cases and the
concrete infrastructure classes. It builds a use case from a config
file (transport + repository + source) and hands it to the loop.
Nothing else in the project imports argparse, and no use case
imports a transport or a database.

Actions (``--action``, default ``full``):
  pull     fetch from the source, validate, rename, seal the
           manifest, record STAGED, leave the batch in staging.
  push     upload staged batches to the destination, record
           UPLOADED, delete the staging folders.
  full     pull then push, back to back (today's end-to-end run).
  dry-run  checks access and permissions first (source, staging,
           ledger and log parents, destination incl. a write
           probe), then simulates full without side effects: the
           pull leg runs for real into a temporary staging area,
           the push leg only reports what would upload; nothing
           is uploaded, written to the ledger, deleted, or emailed.
  validate checks the manifest contract in staging: every batch
           folder's manifest parses, its version matches, its
           run_id matches the folder, and every listed file is
           present with matching SHA-256 and size. No transfers,
           no writes, no ledger reads.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fileflow_contract import new_run_id

from .. import __version__
from ..application.dry_run import DryRunUseCase
from ..application.pull import PullUseCase
from ..application.push import PushUseCase
from ..application.validate import ValidateUseCase
from ..domain.integrity import Context
from ..domain.ports import Notifier, RunRepository, Transport
from ..infrastructure.config import (
    AgentConfig,
    ConfigError,
    LocalDirSourceConfig,
    LocalSourceConfig,
    RemoteDirSourceConfig,
    RemoteFilesSourceConfig,
    WebsiteSourceConfig,
    load_config,
)
from ..infrastructure.filesystem import log, setup_logging
from ..infrastructure.notify import SmtpNotifier
from ..infrastructure.persistence import NullRunRepository, SqlAlchemyRunRepository
from ..infrastructure.sources import build_source
from ..infrastructure.transport import build_transport


def _context(cfg: AgentConfig) -> Context:
    """One Context per tick, from the job's variables only. The
    builders run inside the loop, so each tick gets a fresh one."""
    return Context(cfg.variables)


def _build_repository(cfg: AgentConfig, *, create: bool = True) -> RunRepository:
    """The ledger, or nothing: a config without ``db_url`` runs
    without a ledger. The null repository answers every read empty
    and swallows every write, so the use cases need no branch for
    it -- and no database file is ever created. ``create=False``
    is the dry-run mode: the file is read, never created."""
    if cfg.db_url is None:
        return NullRunRepository()
    return SqlAlchemyRunRepository(Path(_context(cfg).local_path(cfg.db_url)), create=create)


def build_pull_use_case(
    cfg: AgentConfig,
    *,
    notifier: Notifier | None = None,
    sleep=None,
    dry_run: bool = False,
):
    """Compose one pull run: (use case, transport, repository).

    Pull needs no transport, so the transport slot is None. The
    caller owns the repository and must close it. A config without
    ``source`` cannot pull. A notifier is built from the config's
    ``notify`` section unless the caller passes one (tests inject
    a fake SMTP)."""
    if cfg.source is None:
        raise ValueError("config has no 'source' section; pull needs one")
    repository = _build_repository(cfg)
    use_case = PullUseCase(
        cfg,
        source=build_source(cfg.source, cfg.variables),
        repository=repository,
        notifier=notifier if notifier is not None else SmtpNotifier(cfg.notify),
        sleep=sleep,
        dry_run=dry_run,
    )
    return use_case, None, repository


def build_push_use_case(
    cfg: AgentConfig,
    *,
    notifier: Notifier | None = None,
    sleep=None,
    dry_run: bool = False,
):
    """Compose one push run: (use case, transport, repository).

    Push needs no source -- a push-only config has none. The
    caller owns the transport and repository and must close them.
    A notifier is built from the config's ``notify`` section unless
    the caller passes one (tests inject a fake SMTP)."""
    transport = build_transport(cfg.destination.transport)
    repository = _build_repository(cfg)
    use_case = PushUseCase(
        cfg,
        transport=transport,
        repository=repository,
        notifier=notifier if notifier is not None else SmtpNotifier(cfg.notify),
        sleep=sleep,
        dry_run=dry_run,
    )
    return use_case, transport, repository


def build_dry_run_use_case(
    cfg: AgentConfig,
    *,
    sleep=None,
):
    """Compose one dry run: (use case, transport, repository).

    A dry run sends no email, so it takes no notifier. A config
    without ``source`` cannot dry-run the pull leg. The repository
    is opened with ``create=False``: a dry run reads the ledger
    but never creates the file."""
    if cfg.source is None:
        raise ValueError("config has no 'source' section; dry-run needs one")
    transport = build_transport(cfg.destination.transport)
    repository = _build_repository(cfg, create=False)
    use_case = DryRunUseCase(
        cfg,
        source=build_source(cfg.source, cfg.variables),
        transport=transport,
        repository=repository,
        sleep=sleep,
    )
    return use_case, transport, repository


@contextmanager
def _opened(build):
    """Build a use case, yield it, and always close its transport
    and repository -- the caller owns neither after this. The
    transport slot is None for use cases that need none (pull)."""
    use_case, transport, repository = build()
    try:
        yield use_case
    finally:
        if transport is not None:
            transport.close()
        repository.close()


def _print_dry_run(result: dict) -> None:
    """The dry-run report: what would have happened."""
    pulled = result["pulled"]
    if pulled["status"] == "SKIPPED":
        print(
            f"dry run: pull: nothing fresh, SKIPPED "
            f"({len(pulled['skipped'])} file(s) already processed)"
        )
    else:
        print(f"dry run: pull would stage {len(pulled['outputs'])} file(s):")
        for name in pulled["outputs"]:
            print(f"  - {name}")
    for run_id, names, _ in result["would_upload"]:
        print(
            f"dry run: push would upload batch {run_id}: "
            f"{', '.join(names)} -> {result['destination']}"
        )
    for run_id, reason in result["would_fail"]:
        print(f"dry run: push would fail batch {run_id}: {reason}")
    if not result["would_upload"] and not result["would_fail"]:
        print("dry run: push would upload nothing")
    print("dry run: no uploads, no ledger writes, no deletes, no emails")


@dataclass
class CheckResult:
    """One dry-run access/permission check: pass/fail plus the
    detail, and warnings that don't fail the check (a probe file
    the delete could not remove)."""

    label: str
    ok: bool
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


def _probe_writable(directory: Path, run_id: str) -> tuple[bool, str, list[str]]:
    """Prove a directory is writable: write a probe file, delete it
    again. Returns (ok, detail, warnings). A delete failure leaves
    a leftover probe file behind, so it is a warning, not a
    failure -- the write itself proved the directory writable."""
    name = f".fileflow-write-probe-{run_id}"
    probe = directory / name
    try:
        probe.write_bytes(b"fileflow dry-run write probe\n")
    except OSError as e:
        return False, f"cannot write in {directory}: {e}", []
    try:
        probe.unlink()
    except OSError as e:
        return True, "", [f"probe file left in {directory}: {name} ({e})"]
    return True, "", []


def _check_source_access(
    cfg: AgentConfig,
    ctx: Context,
    source_transport: Transport | None,
) -> CheckResult:
    """Source reachable and listable, read-only. A source type with
    no lightweight check (smb, selenium, playwright) passes with a
    note -- the pull leg then fails naturally if it is broken."""
    source = cfg.source
    if source is None:
        return CheckResult("source", True, "no source configured")
    if isinstance(source, (RemoteDirSourceConfig, RemoteFilesSourceConfig)):
        transport = source_transport
        owned = transport is None
        try:
            if transport is None:
                transport = build_transport(source.transport)
            if isinstance(source, RemoteDirSourceConfig):
                dirs = [source.remote_dir]
            else:
                dirs = sorted({ctx.remote_path(f.directory) for f in source.files})
            counts = [len(transport.list_names(d)) for d in dirs]
        except Exception as e:  # noqa: BLE001 -- a failed check reports any failure
            return CheckResult("source", False, f"{source.type} unreachable: {e}")
        finally:
            if owned and transport is not None:
                transport.close()
        detail = ", ".join(f"{n} entries in {d}" for d, n in zip(dirs, counts))
        return CheckResult("source", True, f"{source.type}: {detail}")
    if isinstance(source, LocalSourceConfig):
        path = Path(source.path)
        if not path.is_file():
            return CheckResult("source", False, f"no such file: {source.path}")
        if not os.access(path, os.R_OK):
            return CheckResult("source", False, f"not readable: {source.path}")
        return CheckResult("source", True, f"local file {source.path} readable")
    if isinstance(source, LocalDirSourceConfig):
        directory = Path(source.directory)
        if not directory.is_dir():
            return CheckResult("source", False, f"no such directory: {source.directory}")
        try:
            names = os.listdir(directory)
        except OSError as e:
            return CheckResult("source", False, f"cannot list {source.directory}: {e}")
        return CheckResult("source", True, f"local-dir {source.directory}: {len(names)} entries")
    if isinstance(source, WebsiteSourceConfig):
        try:
            req = urllib.request.Request(
                source.url,
                headers={"User-Agent": f"FileFlow-Agent/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=source.timeout_seconds) as resp:
                return CheckResult("source", True, f"website: HTTP {resp.status} {source.url}")
        except Exception as e:  # noqa: BLE001 -- a failed check reports any failure
            return CheckResult("source", False, f"website unreachable: {e}")
    return CheckResult(
        "source", True, f"{source.type}: not checked (no lightweight connectivity check)"
    )


def _check_destination(
    cfg: AgentConfig,
    ctx: Context,
    transport: Transport | None,
    run_id: str,
) -> CheckResult:
    """Destination: connect + login, write permission via a probe upload that
    is deleted again. The probe goes through the transport's
    upload() + delete(), so it works for local/ftp/sftp alike; it
    is never added to a manifest or the ledger. delete() swallows
    its own errors on every transport, so the probe is verified
    gone with list_names afterwards -- a leftover is a warning.

    The probe upload decides whether the destination is usable:
    local push creates a missing directory on upload, so a missing
    directory is not a failure here (the detail says so); FTP/SFTP
    upload never creates one, so a missing directory fails the
    probe the same way it would fail a real push."""
    remote_dir = (
        ctx.remote_path(cfg.destination.directory)
        if cfg.destination.directory
        else f"incoming/{cfg.job}"
    )
    owned = transport is None
    try:
        if transport is None:
            transport = build_transport(cfg.destination.transport)
    except Exception as e:  # noqa: BLE001 -- a failed check reports any failure
        return CheckResult("destination", False, f"cannot connect: {e}")
    try:
        # Whether the directory existed before the probe:
        # informational only, and only meaningful for local -- it is
        # the one transport whose upload creates directories.
        existed = (
            Path(cfg.destination.transport.root, remote_dir).is_dir()
            if cfg.destination.transport.kind == "local"
            else True
        )
        try:
            names = transport.list_names(remote_dir)
        except Exception as e:  # noqa: BLE001 -- a failed check reports any failure
            return CheckResult("destination", False, f"cannot list {remote_dir}: {e}")
        probe_name = f".fileflow-write-probe-{run_id}"
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / probe_name
            probe.write_bytes(b"fileflow dry-run write probe\n")
            try:
                transport.upload(probe, remote_dir, probe_name)
            except Exception as e:  # noqa: BLE001 -- a failed check reports any failure
                return CheckResult("destination", False, f"write probe failed: {e}")
            transport.delete(remote_dir, probe_name)
            warnings = (
                [f"probe file left in {remote_dir}: {probe_name}"]
                if probe_name in transport.list_names(remote_dir)
                else []
            )
        detail = f"{len(names)} entries in {remote_dir}; write probe uploaded and removed"
        if not existed:
            detail += " (directory did not exist, created by probe; push creates it)"
        return CheckResult("destination", True, detail, warnings)
    finally:
        if owned:
            transport.close()


def dry_run_checks(
    cfg: AgentConfig,
    *,
    destination_transport: Transport | None = None,
    source_transport: Transport | None = None,
) -> list[CheckResult]:
    """Access/permission checks for a dry run, in order: source,
    staging, ledger parent, log-file parent (when configured),
    destination. Every check runs and reports, even after a
    failure, so one dry run shows the whole picture. Transports
    passed in are the caller's (tests inject fakes); transports
    built here are closed here."""
    ctx = _context(cfg)
    run_id = new_run_id()
    results = [_check_source_access(cfg, ctx, source_transport)]
    staging_root = Path(ctx.local_path(cfg.staging_dir))
    created = False
    if not staging_root.exists():
        try:
            staging_root.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as e:
            results.append(CheckResult("staging", False, f"cannot create {staging_root}: {e}"))
    if staging_root.exists():
        if not staging_root.is_dir():
            results.append(CheckResult("staging", False, f"not a directory: {staging_root}"))
        else:
            ok, detail, warnings = _probe_writable(staging_root, run_id)
            results.append(
                CheckResult(
                    "staging",
                    ok,
                    f"{staging_root} writable" + (" (created)" if created else "")
                    if ok
                    else detail,
                    warnings,
                )
            )
    if cfg.db_url is None:
        # No ledger configured: nothing to probe, and the check
        # passes -- the pull leg runs without dedup, by design.
        results.append(CheckResult("ledger", True, "not configured (no db_url; no ledger)"))
    else:
        ledger_parent = Path(ctx.local_path(cfg.db_url)).parent
        if not ledger_parent.is_dir():
            results.append(
                CheckResult("ledger", False, f"ledger directory does not exist: {ledger_parent}")
            )
        else:
            ok, detail, warnings = _probe_writable(ledger_parent, run_id)
            results.append(
                CheckResult("ledger", ok, f"{ledger_parent} writable" if ok else detail, warnings)
            )
    if cfg.log_file:
        log_parent = Path(ctx.local_path(cfg.log_file)).parent
        if not log_parent.is_dir():
            results.append(
                CheckResult("log_file", False, f"log directory does not exist: {log_parent}")
            )
        else:
            ok, detail, warnings = _probe_writable(log_parent, run_id)
            results.append(
                CheckResult("log_file", ok, f"{log_parent} writable" if ok else detail, warnings)
            )
    results.append(_check_destination(cfg, ctx, destination_transport, run_id))
    return results


def run_validate(cfg: AgentConfig) -> int:
    """Validate the manifest contract in staging. Exit 0 when every
    batch is valid (or nothing is staged), 1 when any batch is
    invalid or incomplete. A config error never reaches here --
    main() parses first and exits 2. No ledger reads, no transfers,
    no writes, no emails."""
    dest = cfg.destination.transport
    where = dest.root if dest.kind == "local" else f"{dest.host}:{dest.port}"
    source_desc = cfg.source.type if cfg.source is not None else "none"
    print(f"config OK: job {cfg.job!r}, source {source_desc}, destination {dest.kind} {where}")
    result = ValidateUseCase(cfg).run()
    for batch in result["batches"]:
        if batch["status"] == "OK":
            print(f"validate: batch {batch['run_id']}: OK")
        elif batch["status"] == "SKIPPED":
            print(f"validate: batch {batch['run_id']}: skipped ({batch['problems'][0]})")
        elif batch["status"] == "INCOMPLETE":
            print(f"validate: batch {batch['run_id']}: INCOMPLETE (no manifest)")
        else:
            print(f"validate: batch {batch['run_id']}: FAILED: {'; '.join(batch['problems'])}")
    if not result["batches"]:
        print("validate: nothing staged to validate")
    elif result["status"] == "VALID":
        ok = sum(1 for b in result["batches"] if b["status"] == "OK")
        print(f"validate: OK ({ok} batch(es) valid)")
    else:
        bad = sum(1 for b in result["batches"] if b["status"] in ("FAILED", "INCOMPLETE"))
        print(f"validate: FAILED ({bad} problem batch(es))")
    return 0 if result["status"] == "VALID" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Agent (Ethernet PC)")
    ap.add_argument("--config", required=True)
    ap.add_argument(
        "--action",
        choices=["pull", "push", "full", "dry-run", "validate"],
        default="full",
        help="pull: fetch + stage; push: upload staged batches; "
        "full: pull then push (default); dry-run: check access "
        "and permissions, then simulate full without side effects; "
        "validate: check the manifest contract in staging",
    )
    ap.add_argument("--loop", type=int, default=0, help="repeat every N seconds; 0 = run once")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        # A broken config must fail loud here, before the loop --
        # never retry once a minute and log the same error forever.
        print(f"config error: {e}", file=sys.stderr)
        return 2
    # Logging is set up after the config loads, so the config's
    # log_file (when set) takes effect from the first run. log_file
    # is a template: it is resolved here and again each tick.
    setup_logging(
        args.log_level,
        log_file=_context(cfg).local_path(cfg.log_file) if cfg.log_file else None,
    )
    action = args.action
    if action == "validate":
        # One-shot staging check; --loop does not apply.
        return run_validate(cfg)
    if action == "dry-run":
        # One-shot simulation; --loop does not apply. Access and
        # permission checks run first; any failure stops the run
        # before the pull leg, and the failed check is named.
        if cfg.source is None:
            print("config error: dry-run needs a 'source' section", file=sys.stderr)
            return 2
        results = dry_run_checks(cfg)
        for r in results:
            if r.ok:
                print(f"dry run check: {r.label}: OK" + (f" -- {r.detail}" if r.detail else ""))
            else:
                print(f"dry run check: {r.label}: FAILED: {r.detail}")
            for w in r.warnings:
                print(f"dry run check: {r.label}: warning: {w}")
        failed = [r.label for r in results if not r.ok]
        if failed:
            print(
                f"dry run: {len(failed)} check(s) failed: {', '.join(failed)}",
                file=sys.stderr,
            )
            return 1
        try:
            with _opened(lambda: build_dry_run_use_case(cfg)) as use_case:
                result = use_case.run()
        except Exception as e:  # noqa: BLE001 -- a failed dry run is a failed run
            log.error("dry run failed: %s", e)
            return 1
        _print_dry_run(result)
        return 0
    if action in ("pull", "full") and cfg.source is None:
        # Same class of failure as a bad config: fail loud here,
        # before the loop, never as a per-run error.
        print("config error: pull/full needs a 'source' section", file=sys.stderr)
        return 2
    while True:
        failed = False
        try:
            if cfg.log_file:
                setup_logging(args.log_level, log_file=_context(cfg).local_path(cfg.log_file))
            if action in ("pull", "full"):
                with _opened(lambda: build_pull_use_case(cfg)) as use_case:
                    use_case.run()
            if action in ("push", "full"):
                with _opened(lambda: build_push_use_case(cfg)) as use_case:
                    use_case.run()
        except Exception as e:  # noqa: BLE001 -- a failed run must not kill the loop
            log.error("agent run failed: %s", e)
            failed = True
        if not args.loop:
            return 1 if failed else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
