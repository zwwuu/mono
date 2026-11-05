# FileFlow — generic task registry.
#
# A task is a named, reusable file-processing pipeline declared in JSON,
# not code. Adding, removing or changing a task means editing tasks.json
# (by hand, or through the dashboard/API) — no code change, no restart:
# the registry reloads the file whenever it changes on disk.
#
# Task shape:
#   {
#     "id": "daily-report",            # unique, [a-z0-9_-]+
#     "name": "Daily Report",
#     "enabled": true,
#     "source": {"type": "website", "url": "https://.../report.csv"}
#       or {"type": "local", "path": "..."} (one file) or
#          {"type": "local", "dir": "...", "pattern": "*.csv", "sort": "newest"}
#          (pick a file from a directory) or
#          {"type": "ftp", "dir": "...", "pattern": ..., "sort": ...} or
#          {"type": "smb", "dir": ..., "pattern": ..., "sort": ...}
#          (pick a file from an FTP directory / a share) or
#          {"type": "browser", "actions": [...]}
#          (drive a web page: login, click, download — see
#          infra/browser_fetch.py; needs the "browser" extra)
#             | {"type": "local", "path": "relative/or/absolute/path"},
#     "steps": [ ... ]               # optional; see STEP_TYPES below
#   }
#
# When "steps" is omitted, the pipeline is built from shorthand fields:
#   "approval": true|false (default true: one gate after checksum),
#   "approval_after": ["fetch", "rename", "copy", ...] (gates after the
#     named steps — a task can require several approvals, or none),
#   "rename": "{date}_{name}{ext}" (template, see steps.render_rename),
#   "destinations": [{"type": "ftp"|"local"|"smb", "dir": "..."}],
#   "processing": {"dir": "processed", "pattern": "{renamed_name}",
#                  "timeout_seconds": 300, "poll_seconds": 5},
#   "notify": true|false|{...} (default true; a dict carries notify-step
#     params: template, subject, recipients, when),
#   "success_template"/"success_subject": templates for the shorthand
#     completion mail; "failure_template"/"failure_subject": templates
#     for stuck mail (any step may also carry its own failure_* pair,
#     which wins for failures at that step),
#   "recipients": [ ... ] (overrides the global notify list for this task).
#
# Step types (each step is {"type": ..., ...params}):
#   fetch            copy the task source into the run workdir
#   checksum         SHA-256 of the fetched file
#   approval         pause for a human Approve/Reject on the dashboard
#                    (a task may have several, at any positions)
#   rename           rename the current file; param "pattern" (template)
#   copy             copy the current file; param "to": {"type", "dir"}
#   wait_processed   wait for a processed file on FTP; params "dir",
#                    "pattern", "timeout_seconds", "poll_seconds"
#   fetch_processed  download the processed file; it becomes current
#   notify           send an email; params "template", "subject",
#                    "recipients", "when" (a condition, see steps.eval_when)
#
# The "current file" flows through the pipeline: fetch sets it, rename
# replaces it, fetch_processed replaces it again, and copy steps copy
# whatever is current at that point. So a task that processes on FTP
# copies the processed file onward, while a task without processing
# copies the renamed file.
import json
import os
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from common.pipeline.builder import (
    DEFAULT_RENAME,
    TaskError,
    base_shorthand_steps,
    step_keys,
    validate_rename_pattern,
)

# ------------------------------------------------------- Pydantic models
# Task shape, validated by Pydantic at the registry boundary (load/save
# and the dashboard/API). The models enforce exactly what the old
# hand-written checks enforced — same rules, same rejections — with two
# deliberate tightenings: shorthand fields (destinations, notify, ...) are
# now type-checked even when explicit "steps" are present (previously
# ignored in that case), and "run_limit.max" is a strict int (the string
# "3" no longer coerces). Everything the models cannot express — rename
# pattern rendering, browser action shapes, approval_after points — stays
# as semantic checks in validate_task below.
#
# The registry keeps plain dicts: models validate, they do not replace
# the task representation the engine and pipeline builder consume.


class RunLimitModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    max: StrictInt = Field(gt=0)
    period: Literal["day", "week", "month", "total"] = "day"


class CopyTargetModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["ftp", "local", "smb"]
    dir: str

    @model_validator(mode="after")
    def _non_empty_dir(self):
        if self.type in ("ftp", "local") and not self.dir:
            raise ValueError(f"copy target {self.type!r} needs a non-empty 'dir'")
        return self


class WhenModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    field: str
    op: Literal["exists", "eq", "ne", "gt", "lt", "ge", "le", "contains"] = "exists"
    value: Any = None


class NotifyParamsModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    template: str | None = None
    subject: str | None = None
    recipients: list[str] | None = None
    when: WhenModel | None = None


class StepBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    failure_template: str | None = None
    failure_subject: str | None = None


class FetchStep(StepBase):
    type: Literal["fetch"]


class ChecksumStep(StepBase):
    type: Literal["checksum"]


class ApprovalStep(StepBase):
    type: Literal["approval"]


class RenameStep(StepBase):
    type: Literal["rename"]
    pattern: str = DEFAULT_RENAME


class CopyStep(StepBase):
    type: Literal["copy"]
    to: CopyTargetModel


class WaitProcessedStep(StepBase):
    type: Literal["wait_processed"]
    dir: str = Field(min_length=1)


class FetchProcessedStep(StepBase):
    type: Literal["fetch_processed"]


class NotifyStep(StepBase):
    type: Literal["notify"]
    template: str | None = None
    subject: str | None = None
    recipients: list[str] | None = None
    when: WhenModel | None = None


StepModel = Annotated[
    Union[
        FetchStep,
        ChecksumStep,
        ApprovalStep,
        RenameStep,
        CopyStep,
        WaitProcessedStep,
        FetchProcessedStep,
        NotifyStep,
    ],
    Field(discriminator="type"),
]


class WebsiteSourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["website"]
    url: str = Field(min_length=1)


class LocalSourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["local"]
    path: str | None = None
    dir: str | None = None
    pattern: str | None = None
    sort: Literal["newest", "oldest", "name"] | None = None

    @model_validator(mode="after")
    def _path_or_dir(self):
        if not self.path and not self.dir:
            raise ValueError("local source needs a 'path' or a 'dir'")
        return self


class FtpSourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["ftp"]
    dir: str | None = None
    pattern: str | None = None
    sort: Literal["newest", "oldest", "name"] | None = None


class SmbSourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["smb"]
    dir: str | None = None
    pattern: str | None = None
    sort: Literal["newest", "oldest", "name"] | None = None


class BrowserSourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["browser"]
    actions: list
    headless: StrictBool | None = None


SourceModel = Annotated[
    Union[
        WebsiteSourceModel,
        LocalSourceModel,
        FtpSourceModel,
        SmbSourceModel,
        BrowserSourceModel,
    ],
    Field(discriminator="type"),
]


class TaskModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    run_limit: RunLimitModel | None = None
    source: SourceModel | None = None
    steps: list[StepModel] | None = None
    rename: str | None = None
    destinations: list[CopyTargetModel] = []
    success_template: str | None = None
    success_subject: str | None = None
    failure_template: str | None = None
    failure_subject: str | None = None
    recipients: list[str] | None = None
    notify: StrictBool | NotifyParamsModel | None = None
    approval_after: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v):
        if not v.strip():
            raise ValueError("task needs a non-empty 'name'")
        return v

    @model_validator(mode="after")
    def _steps_non_empty_with_fetch(self):
        if self.steps is not None:
            if not self.steps:
                raise ValueError("'steps' must be a non-empty list")
            if not any(s.type == "fetch" for s in self.steps):
                raise ValueError("steps must include a 'fetch' step")
        return self


def _pydantic_errors(e):
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())


def default_tasks_path(cfg):
    """tasks.json lives next to the config file unless configured."""
    configured = cfg.get("app", "tasks_file", "")
    if configured:
        return (
            configured
            if os.path.isabs(configured)
            else os.path.join(os.path.dirname(cfg.source_path or "./config.json"), configured)
        )
    base = os.path.dirname(cfg.source_path) if cfg.source_path else "."
    return os.path.join(base, "tasks.json")


def validate_task(task, existing_ids=()):
    """Validate a task dict. Returns the task unchanged.

    Structure is validated by the Pydantic models above; the checks
    below are the semantic ones models cannot express (rename rendering,
    browser action shapes, approval_after points). The registry keeps
    plain dicts — models validate, they do not replace the representation.
    """
    if not isinstance(task, dict):
        raise TaskError("task must be an object")
    tid = task.get("id")
    if isinstance(tid, str) and tid in existing_ids:
        raise TaskError(f"duplicate task id: {tid!r}")
    try:
        TaskModel.model_validate(task)
    except ValidationError as e:
        raise TaskError(f"task {tid!r} is invalid: {_pydantic_errors(e)}") from e
    steps = task.get("steps")
    if steps is not None:
        for step in steps:
            if step.get("type") == "rename":
                validate_rename_pattern(step.get("pattern", DEFAULT_RENAME))
    elif task.get("rename") is not None:
        validate_rename_pattern(task["rename"])
    source = task.get("source")
    if isinstance(source, dict) and source.get("type") == "browser":
        from common.pipeline.browser_fetch import BrowserSourceError, validate_actions

        try:
            validate_actions(source.get("actions"))
        except BrowserSourceError as e:
            raise TaskError(f"task {tid!r} browser source: {e}") from e
    if "approval_after" in task:
        points = task["approval_after"]
        base = base_shorthand_steps(task)
        keys, types = step_keys(base)
        for point in points:
            if point not in keys and point not in types:
                raise TaskError(f"task {tid!r} approval_after point {point!r} matches no step")
    return task


class TaskRegistry:
    """tasks.json on disk, reloaded when the file changes."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = default_tasks_path(cfg)
        self._tasks = []
        self._mtime = None

    # ------------------------------------------------------------ loading
    def _load_from_disk(self):
        if not os.path.exists(self.path):
            return None
        mtime = os.path.getmtime(self.path)
        if self._mtime == mtime:
            return self._tasks
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
            raise TaskError(f'{self.path} must contain {{"tasks": [...]}}')
        tasks = []
        for t in raw["tasks"]:
            validate_task(t, [x["id"] for x in tasks])
            tasks.append(t)
        self._tasks = tasks
        self._mtime = mtime
        return tasks

    def all(self):
        """All tasks; an empty list when tasks.json does not exist yet."""
        tasks = self._load_from_disk()
        return list(tasks) if tasks is not None else []

    def get(self, task_id):
        for t in self.all():
            if t["id"] == task_id:
                return t
        return None

    # ------------------------------------------------------------- writing
    def _write(self, tasks):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)
        self._tasks = list(tasks)
        self._mtime = os.path.getmtime(self.path)

    def create(self, task):
        tasks = self.all()
        validate_task(task, [t["id"] for t in tasks])
        tasks.append(task)
        self._write(tasks)
        return task

    def update(self, task_id, task):
        tasks = self.all()
        if task.get("id", task_id) != task_id:
            raise TaskError("task id cannot be changed")
        task["id"] = task_id
        others = [t["id"] for t in tasks if t["id"] != task_id]
        validate_task(task, others)
        for i, t in enumerate(tasks):
            if t["id"] == task_id:
                tasks[i] = task
                self._write(tasks)
                return task
        raise TaskError(f"task not found: {task_id!r}")

    def delete(self, task_id):
        tasks = self.all()
        kept = [t for t in tasks if t["id"] != task_id]
        if len(kept) == len(tasks):
            raise TaskError(f"task not found: {task_id!r}")
        self._write(kept)
        return True

    def set_enabled(self, task_id, enabled):
        task = self.get(task_id)
        if task is None:
            raise TaskError(f"task not found: {task_id!r}")
        task["enabled"] = bool(enabled)
        return self.update(task_id, task)
