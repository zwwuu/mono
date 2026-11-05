"""Shared file-processing pipeline library.

Moved out of FileFlow so standalone apps (the ff-* scenario apps) can
use it without importing from another app. FileFlow's engine uses the
same modules; its pydantic task validation and task registry stay in
FileFlow.

browser_fetch is deliberately NOT imported here: it needs Playwright,
and steps.py imports it lazily only for browser sources.
"""

from .builder import (
    COPY_TYPES,
    DEFAULT_RENAME,
    RUN_LIMIT_PERIODS,
    SOURCE_TYPES,
    STEP_TYPES,
    TaskError,
    base_shorthand_steps,
    build_pipeline,
    step_keys,
    validate_rename_pattern,
)
from .headless import HeadlessConfig, run_pipeline
from .steps import (
    STEP_FUNCS,
    STEP_LABELS,
    StepContext,
    StepError,
    render_rename,
    render_template,
)

__all__ = [
    "COPY_TYPES",
    "DEFAULT_RENAME",
    "RUN_LIMIT_PERIODS",
    "SOURCE_TYPES",
    "STEP_FUNCS",
    "STEP_LABELS",
    "STEP_TYPES",
    "HeadlessConfig",
    "StepContext",
    "StepError",
    "TaskError",
    "base_shorthand_steps",
    "build_pipeline",
    "render_rename",
    "render_template",
    "run_pipeline",
    "step_keys",
    "validate_rename_pattern",
]
