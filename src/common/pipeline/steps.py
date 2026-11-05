# FileFlow — parameterized run steps.
#
# Steps are generic: each receives a StepContext carrying the task being
# run and the step's own params (from tasks.json), and returns a short
# result message. Raise StepError for a normal failure, StuckError when
# the step cannot make progress and a human should look (both mark the
# step STUCK and trigger the stuck email + dashboard Retry button).
#
# The "current file" flows through the pipeline: fetch sets it, rename
# replaces it, fetch_processed replaces it again, and copy steps copy
# whatever is current at that point.
import hashlib
import os
import re
import shutil
import time
from datetime import datetime

from . import transfers
from .notify import send_email


class StepError(Exception):
    pass


class StuckError(StepError):
    pass


class StepContext:
    def __init__(self, cfg, run, log, task, params, key=""):
        self.cfg = cfg
        self.run = run  # dict row from db (context parsed)
        self.data = run["context"]  # mutable dict, persisted by engine
        self.log = log  # callable(msg, level="INFO")
        self.run_id = run["id"]
        self.task = task or {}
        self.params = params or {}
        self.key = key

    def workdir(self, *parts):
        d = self.cfg.path("work", f"run-{self.run_id}", *parts)
        os.makedirs(d, exist_ok=True)
        return d


# ------------------------------------------------------------------ rename
_TEMPLATE_TOKEN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def render_template(template, tokens):
    """Render a {token} template; unknown tokens are left literal so a
    typo in a template never breaks a run."""
    return _TEMPLATE_TOKEN.sub(lambda m: str(tokens.get(m.group(1), m.group(0))), template)


def notify_tokens(ctx, error=""):
    """Tokens available in success/failure email templates."""
    d = ctx.data
    return {
        "task": ctx.task.get("id", ""),
        "task_name": ctx.task.get("name", ""),
        "run_id": str(ctx.run_id),
        "step": ctx.key,
        "error": error or "",
        "current_name": d.get("current_name", ""),
        "current_path": d.get("current_path", ""),
        "downloaded_name": d.get("downloaded_name", ""),
        "downloaded_size": str(d.get("downloaded_size", "")),
        "sha256": d.get("sha256", ""),
        "renamed_name": d.get("renamed_name", ""),
        "processed_name": d.get("processed_name", ""),
        "processed_size": str(d.get("processed_size", "")),
        "processed_sha256": d.get("processed_sha256", ""),
    }


def eval_when(ctx, when):
    """Evaluate a notify-step condition, e.g.
    {"field": "downloaded_size", "op": "gt", "value": 1000000}.
    Ops: exists, eq, ne, gt, lt, ge, le, contains. Absent condition = True."""
    if not when:
        return True
    field = when.get("field", "")
    op = when.get("op", "exists")
    if field == "current_size":
        path = ctx.data.get("current_path", "")
        value = os.path.getsize(path) if path and os.path.exists(path) else None
    else:
        value = ctx.data.get(field)
    if op == "exists":
        return value is not None and value != ""
    if value is None:
        return False
    want = when.get("value")
    if op == "eq":
        return str(value) == str(want)
    if op == "ne":
        return str(value) != str(want)
    if op == "contains":
        return str(want) in str(value)
    if op in ("gt", "lt", "ge", "le"):
        try:
            a, b = float(value), float(want)
        except (ValueError, TypeError) as _e:
            return False
        return {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b}[op]
    raise StepError(f"unknown when op: {op!r}")


def render_rename(pattern, original, sha256="", run_id=0, task_id=""):
    """Render a rename template; result must be a plain filename."""
    name, ext = os.path.splitext(original)
    now = datetime.now()
    tokens = {
        "original": original,
        "name": name,
        "ext": ext,
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "datetime": now.strftime("%Y%m%d_%H%M%S"),
        "checksum": sha256,
        "checksum8": sha256[:8],
        "run_id": str(run_id),
        "task": task_id,
    }
    return pattern.format(**tokens)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ steps
def step_fetch(ctx):
    """Fetch the task source into the run workdir.

    Source types:
      website: {"type": "website", "url": ...} (http download; in demo
        mode with no url, the sample file in demo_source/ is used)
      local:   {"type": "local", "path": ...} (one file) or
               {"type": "local", "dir": ..., "pattern": "*.csv",
                "sort": "newest"|"oldest"|"name"} (pick from a directory)
      ftp:     {"type": "ftp", "dir": ..., "pattern": ..., "sort": ...}
               (pick from an FTP directory; demo mode reads demo_ftp/)
      smb:     {"type": "smb", "dir": ..., "pattern": ..., "sort": ...}
               (pick from the share; demo mode reads demo_smb/)
      browser: {"type": "browser", "actions": [...]} (drive a web page:
        login, click, download — see common.pipeline.browser_fetch)
    """
    source = ctx.task.get("source") or {}
    stype = source.get("type", "website")
    input_dir = ctx.workdir("input")
    if stype == "website" and ctx.cfg.get("demo", "enabled", False) and not source.get("url"):
        # demo "website": a sample file in demo_source/
        src = ctx.cfg.path("demo_source", ctx.cfg.get("demo", "source_file", "input_report.csv"))
        if not os.path.exists(src):
            raise StepError(f"demo source file missing: {src}")
        url = "file://" + src
        name = os.path.basename(src)
        dest = os.path.join(input_dir, name)
        ctx.log(f"Fetching {url}")
        size = transfers.download_url(
            url, dest, timeout=int(ctx.cfg.get("source", "timeout_seconds", 60))
        )
    elif stype == "website":
        url = source.get("url", "")
        if not url:
            raise StepError("task source.url is empty")
        name = os.path.basename(url.split("?")[0]) or "download.bin"
        dest = os.path.join(input_dir, name)
        ctx.log(f"Fetching {url}")
        size = transfers.download_url(
            url, dest, timeout=int(ctx.cfg.get("source", "timeout_seconds", 60))
        )
    elif stype == "local":
        if source.get("path"):
            path = source["path"]
            src = path if os.path.isabs(path) else ctx.cfg.path(path)
            if not os.path.exists(src):
                raise StepError(f"local source file missing: {src}")
            name = os.path.basename(src)
            url = "file://" + src
            dest = os.path.join(input_dir, name)
            ctx.log(f"Fetching {url}")
            shutil.copyfile(src, dest)
            size = os.path.getsize(dest)
        elif source.get("dir"):
            dir_path = source["dir"]
            if not os.path.isabs(dir_path):
                dir_path = ctx.cfg.path(dir_path)
            name, src = transfers.pick_from_local_dir(
                dir_path, source.get("pattern", "*"), source.get("sort", "newest")
            )
            url = "file://" + src
            dest = os.path.join(input_dir, name)
            ctx.log(f"Fetching {url} (picked from {dir_path})")
            shutil.copyfile(src, dest)
            size = os.path.getsize(dest)
        else:
            raise StepError("local source needs a 'path' or a 'dir'")
    elif stype == "ftp":
        name, dest, size = transfers.download_from_ftp(
            ctx.cfg,
            source.get("dir", ""),
            source.get("pattern", "*"),
            source.get("sort", "newest"),
            input_dir,
        )
        url = f"ftp://{source.get('dir', '')}/{name}"
        ctx.log(f"Fetching {url}")
    elif stype == "smb":
        name, dest, size = transfers.download_from_smb(
            ctx.cfg,
            source.get("dir", ""),
            source.get("pattern", "*"),
            source.get("sort", "newest"),
            input_dir,
        )
        url = f"smb://{source.get('dir', '')}/{name}"
        ctx.log(f"Fetching {url}")
    elif stype == "browser":
        from .browser_fetch import run_browser_source

        dest, name, url = run_browser_source(source, input_dir, lambda m: ctx.log(m))
        size = os.path.getsize(dest)
        ctx.log(f"Fetched {name} via browser from {url}")
    else:
        raise StepError(f"unknown source type: {stype!r}")
    ctx.data["source_url"] = url
    ctx.data["downloaded_name"] = name
    ctx.data["downloaded_path"] = dest
    ctx.data["downloaded_size"] = size
    ctx.data["current_name"] = name
    ctx.data["current_path"] = dest
    return f"fetched {name} ({size} bytes)"


def step_checksum(ctx):
    digest = _sha256_file(ctx.data["current_path"])
    ctx.data["sha256"] = digest
    return f"sha256 = {digest}"


def step_approval(ctx):
    # The engine pauses the run before this step and only runs it
    # after a human clicks Approve on the dashboard.
    return "approved by operator"


def step_rename(ctx):
    pattern = ctx.params.get("pattern", "{date}_{name}{ext}")
    src = ctx.data["current_path"]
    try:
        new_name = render_rename(
            pattern,
            os.path.basename(src),
            sha256=ctx.data.get("sha256", ""),
            run_id=ctx.run_id,
            task_id=ctx.task.get("id", ""),
        )
    except KeyError as e:
        raise StepError(f"unknown token in rename pattern: {e}") from e
    if not new_name or "/" in new_name or "\\" in new_name:
        raise StepError(f"rename pattern must produce a plain filename, got {new_name!r}")
    dest = os.path.join(ctx.workdir("renamed"), new_name)
    shutil.copyfile(src, dest)
    ctx.data["renamed_name"] = new_name
    ctx.data["renamed_path"] = dest
    ctx.data["current_name"] = new_name
    ctx.data["current_path"] = dest
    return f"renamed to {new_name}"


def _local_dir(cfg, d):
    if d == "__data_archive__":
        return cfg.path("archive")
    return d if os.path.isabs(d) else cfg.path(d)


def step_copy(ctx):
    to = ctx.params.get("to", {})
    ctype = to.get("type")
    dest_dir = to.get("dir", "")
    src = ctx.data["current_path"]
    name = ctx.data["current_name"]
    if ctype == "ftp":
        with transfers.open_ftp(ctx.cfg) as ftp:
            ftp.upload(src, dest_dir, name)
        return f"copied {name} to ftp:{dest_dir}/"
    if ctype == "local":
        d = _local_dir(ctx.cfg, dest_dir)
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, name)
        shutil.copyfile(src, dest)
        return f"copied {name} to {dest}"
    if ctype == "smb":
        where = transfers.copy_to_smb(ctx.cfg, src, name, subdir=dest_dir)
        return f"copied {name} to {where}"
    raise StepError(f"unknown copy target type: {ctype!r}")


def _processed_name(ctx, pattern):
    return (
        pattern.replace("{renamed_name}", ctx.data.get("renamed_name", ""))
        .replace("{current_name}", ctx.data.get("current_name", ""))
        .replace("{name}", os.path.splitext(ctx.data.get("current_name", ""))[0])
        .replace("{ext}", os.path.splitext(ctx.data.get("current_name", ""))[1])
    )


def step_wait_processed(ctx):
    processed_dir = ctx.params.get("dir", "processed")
    expected = _processed_name(ctx, ctx.params.get("pattern", "{renamed_name}"))
    ctx.data["processed_name"] = expected
    ctx.data["processed_dir"] = processed_dir
    timeout = int(ctx.params.get("timeout_seconds", ctx.cfg.get("steps", "timeout_seconds", 300)))
    poll = int(ctx.params.get("poll_seconds", ctx.cfg.get("ftp", "poll_seconds", 5)))
    deadline = time.time() + timeout
    ctx.log(f"Waiting for {expected} in ftp:{processed_dir}/ (timeout {timeout}s)")
    while True:
        with transfers.open_ftp(ctx.cfg) as ftp:
            names = ftp.list_names(processed_dir)
        if expected in names:
            return f"processed file appeared: {expected}"
        if time.time() > deadline:
            raise StuckError(
                f"processing timed out after {timeout}s: {expected} not in {processed_dir}"
            )
        time.sleep(poll)


def step_fetch_processed(ctx):
    processed_dir = ctx.params.get("dir") or ctx.data.get("processed_dir", "processed")
    dest = os.path.join(ctx.workdir("processed"), ctx.data["processed_name"])
    with transfers.open_ftp(ctx.cfg) as ftp:
        ftp.download(processed_dir, ctx.data["processed_name"], dest)
    ctx.data["processed_path"] = dest
    ctx.data["processed_sha256"] = _sha256_file(dest)
    ctx.data["processed_size"] = os.path.getsize(dest)
    ctx.data["current_name"] = ctx.data["processed_name"]
    ctx.data["current_path"] = dest
    short = ctx.data["processed_sha256"][:16]
    return f"downloaded processed file ({ctx.data['processed_size']} bytes, sha256 {short}…)"


DEFAULT_SUCCESS_SUBJECT = "[FileFlow] Task '{task_name}' run #{run_id} completed"
DEFAULT_SUCCESS_TEMPLATE = (
    "Task {task} ({task_name}) run #{run_id} completed successfully.\n\n"
    "Source            : {downloaded_name} ({downloaded_size} bytes)\n"
    "SHA-256 (source)  : {sha256}\n"
    "Renamed to        : {renamed_name}\n"
    "Processed file    : {processed_name} ({processed_size} bytes)\n"
    "SHA-256 (processed): {processed_sha256}\n"
    "Current file      : {current_name} ({current_path})\n"
)

DEFAULT_FAILURE_SUBJECT = "[FileFlow] Run #{run_id} STUCK at step '{step}'"
DEFAULT_FAILURE_TEMPLATE = (
    "Run #{run_id} (task '{task}') is stuck at step '{step}'.\n\n"
    "Reason: {error}\n\n"
    "Current file: {current_name}\n\n"
    "Open the dashboard to retry the step."
)


def step_notify(ctx):
    params = ctx.params
    if not eval_when(ctx, params.get("when")):
        return "skipped: condition not met"
    tokens = notify_tokens(ctx)
    subject = render_template(params.get("subject", DEFAULT_SUCCESS_SUBJECT), tokens)
    body = render_template(params.get("template", DEFAULT_SUCCESS_TEMPLATE), tokens)
    recipients = params.get("recipients") or ctx.task.get("recipients")
    ok, how = send_email(ctx.cfg, recipients, subject, body)
    if not ok:
        raise StepError(f"completion email failed: {how}")
    return f"email sent ({how})"


STEP_FUNCS = {
    "fetch": step_fetch,
    "checksum": step_checksum,
    "approval": step_approval,
    "rename": step_rename,
    "copy": step_copy,
    "wait_processed": step_wait_processed,
    "fetch_processed": step_fetch_processed,
    "notify": step_notify,
}

STEP_LABELS = {
    "fetch": "Fetch file from source",
    "checksum": "Create checksum (SHA-256)",
    "approval": "Human approval (dashboard)",
    "rename": "Rename file to target format",
    "copy": "Copy file to destination",
    "wait_processed": "Wait for processing",
    "fetch_processed": "Download processed file",
    "notify": "Send completion email",
}
