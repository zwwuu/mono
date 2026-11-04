"""Per-attempt run context shared between steps."""
from __future__ import annotations


class RunContext:
    """Mutable state for one pipeline attempt.

    Steps read the config and publish their results here
    (``local_path``, the SFTP connection, ...); the pipeline reads it back
    for notifications and cleanup.
    """

    def __init__(self, config, attempt: int):
        self.config = config
        self.attempt = attempt
        self.step: str | None = None
        self.ssh = None
        self.sftp = None
        self.local_path: str | None = None
        self.filename: str | None = None
        self.remote_path: str | None = None
        self.skipped = False
