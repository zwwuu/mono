"""Pipeline construction: expand a task dict into an executable step list.

Pure dict logic, no validation and no I/O. Validation lives in
FileFlow's task models; this module only builds the pipeline that
FileFlow's engine and the standalone ff-* apps execute.

Step types (each step is {"type": ..., ...params}):
  fetch            copy the task source into the run workdir
  checksum         SHA-256 of the fetched file
  approval         pause for a human Approve/Reject on the dashboard
                   (a task may have several, at any positions)
  rename           rename the current file; param "pattern" (template)
  copy             copy the current file; param "to": {"type", "dir"}
  wait_processed   wait for a processed file on FTP; params "dir",
                   "pattern", "timeout_seconds", "poll_seconds"
  fetch_processed  download the processed file; it becomes current
  notify           send an email; params "template", "subject",
                   "recipients", "when" (a condition)

The "current file" flows through the pipeline: fetch sets it, rename
replaces it, fetch_processed replaces it again, and copy steps copy
whatever is current at that point.

When "steps" is omitted, the pipeline is built from shorthand fields:
  "approval": true|false (default true: one gate after checksum),
  "approval_after": ["fetch", "rename", "copy", ...] (gates after the
    named steps — a task can require several approvals, or none),
  "rename": "{date}_{name}{ext}" (template),
  "destinations": [{"type": "ftp"|"local"|"smb", "dir": "..."}],
  "processing": {"dir": "processed", "pattern": "{renamed_name}",
                 "timeout_seconds": 300, "poll_seconds": 5},
  "notify": true|false|{...} (default true; a dict carries notify-step
    params: template, subject, recipients, when).
"""

import re

from . import steps as steps_mod
from .steps import render_rename

STEP_TYPES = (
    "fetch",
    "checksum",
    "approval",
    "rename",
    "copy",
    "wait_processed",
    "fetch_processed",
    "notify",
)

COPY_TYPES = ("ftp", "local", "smb")
SOURCE_TYPES = ("website", "local", "ftp", "smb", "browser")
RUN_LIMIT_PERIODS = ("day", "week", "month", "total")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEFAULT_RENAME = "{date}_{name}{ext}"


class TaskError(ValueError):
    pass


def _is_plain_filename(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def validate_rename_pattern(pattern):
    """Render the pattern with dummy tokens; the result must be a filename."""
    try:
        rendered = render_rename(
            pattern,
            original="input_report.csv",
            sha256="0" * 64,
            run_id=1,
            task_id="dummy-task",
        )
    except (KeyError, ValueError, IndexError) as e:
        raise TaskError(f"invalid rename pattern {pattern!r}: {e}") from e
    if not _is_plain_filename(rendered):
        raise TaskError(
            f"rename pattern {pattern!r} must produce a plain filename, got {rendered!r}"
        )
    return pattern


def build_pipeline(task):
    """Expand a task into [(key, label, fn_name, params)].

    Keys are unique within the pipeline so retry/attempt tracking works
    even with several copy steps.
    """
    raw_steps = task.get("steps")
    if raw_steps is None:
        raw_steps = _shorthand_steps(task)
    pipeline = []
    seen = {}
    for step in raw_steps:
        stype = step["type"]
        seen[stype] = seen.get(stype, 0) + 1
        key = stype if seen[stype] == 1 else f"{stype}:{seen[stype]}"
        if stype == "copy":
            to = step["to"]
            key = f"copy:{to['type']}:{to['dir']}"
            label = f"Copy to {to['type']}:{to['dir']}"
        else:
            label = steps_mod.STEP_LABELS[stype]
        pipeline.append((key, label, stype, dict(step)))
    return pipeline


def step_keys(steps):
    """(keys, types) for a raw step list, using build_pipeline's key rule."""
    keys, types = [], []
    seen = {}
    for step in steps:
        stype = step["type"]
        types.append(stype)
        if stype == "copy":
            keys.append(f"copy:{step['to']['type']}:{step['to']['dir']}")
        else:
            seen[stype] = seen.get(stype, 0) + 1
            keys.append(stype if seen[stype] == 1 else f"{stype}:{seen[stype]}")
    return keys, types


def _insert_approvals(steps, points):
    """Insert an approval step after every step whose key or type is in
    points ('copy' matches every copy step; 'copy:local:arc' one of them)."""
    keys, _types = step_keys(steps)
    out = []
    for step, key in zip(steps, keys):
        out.append(step)
        if key in points or step["type"] in points:
            out.append({"type": "approval"})
    return out


def _notify_params(task):
    """Params for the shorthand trailing notify step."""
    params = {}
    if isinstance(task.get("notify"), dict):
        params.update(task["notify"])
    if task.get("success_template"):
        params.setdefault("template", task["success_template"])
    if task.get("success_subject"):
        params.setdefault("subject", task["success_subject"])
    if task.get("recipients"):
        params.setdefault("recipients", task["recipients"])
    return params


def base_shorthand_steps(task):
    """The shorthand pipeline before approval insertion."""
    steps = [{"type": "fetch"}, {"type": "checksum"}]
    steps.append({"type": "rename", "pattern": task.get("rename", DEFAULT_RENAME)})
    dests = list(task.get("destinations", []))
    processing = task.get("processing")
    if processing:
        ftp_dests = [d for d in dests if d["type"] == "ftp"]
        inbox = ftp_dests[0] if ftp_dests else {"type": "ftp", "dir": "inbox"}
        steps.append({"type": "copy", "to": inbox})
        steps.append(
            {
                "type": "wait_processed",
                "dir": processing.get("dir", "processed"),
                "pattern": processing.get("pattern", "{renamed_name}"),
                "timeout_seconds": processing.get("timeout_seconds", 300),
                "poll_seconds": processing.get("poll_seconds", 5),
            }
        )
        steps.append({"type": "fetch_processed"})
        rest = [d for d in dests if d is not inbox] if ftp_dests else dests
        for d in rest:
            steps.append({"type": "copy", "to": d})
    else:
        for d in dests:
            steps.append({"type": "copy", "to": d})
    if task.get("notify", True):
        steps.append({"type": "notify", **_notify_params(task)})
    return steps


def _shorthand_steps(task):
    steps = base_shorthand_steps(task)
    if "approval_after" in task:
        # approval points are explicit: no default approval is added
        return _insert_approvals(steps, task["approval_after"])
    if task.get("approval", True):
        # the classic single gate, right after checksum
        out = []
        for step in steps:
            out.append(step)
            if step["type"] == "checksum":
                out.append({"type": "approval"})
        return out
    return steps
