"""The manifest document: format, dataclasses, atomic read/write.

Pure stdlib. A manifest is a JSON document listing the transferred
files with their SHA-256 + size, plus the run/job identity.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

PART_SUFFIX = ".part"


@dataclass(frozen=True)
class ManifestFile:
    """One file listed in a manifest: name + role + expected bytes."""

    name: str
    role: str
    size: int
    sha256: str

    def to_dict(self) -> dict:
        return {"name": self.name, "role": self.role, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict) -> ManifestFile:
        return cls(name=d["name"], role=d["role"], size=d["size"], sha256=d["sha256"])


@dataclass
class Manifest:
    """The transfer contract. ``extra`` carries the per-flow fields
    (source_url, procedure, source_run, ...) so the core stays stable
    while either side can attach provenance without a schema change.
    """

    version: int
    run_id: str
    job: str
    idempotency_key: str
    files: list[ManifestFile] = field(default_factory=list)
    direction: str | None = None
    created_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict[str, Any] = dict(self.extra)
        d.update(
            {
                "version": self.version,
                "run_id": self.run_id,
                "job": self.job,
                "idempotency_key": self.idempotency_key,
                "files": [f.to_dict() for f in self.files],
            }
        )
        if self.direction is not None:
            d["direction"] = self.direction
        if self.created_at is not None:
            d["created_at"] = self.created_at
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Manifest:
        known = {
            "version",
            "run_id",
            "job",
            "idempotency_key",
            "files",
            "direction",
            "created_at",
        }
        return cls(
            version=d["version"],
            run_id=d["run_id"],
            job=d["job"],
            idempotency_key=d["idempotency_key"],
            files=[ManifestFile.from_dict(f) for f in d.get("files", [])],
            direction=d.get("direction"),
            created_at=d.get("created_at"),
            extra={k: v for k, v in d.items() if k not in known},
        )


def write_manifest(path: str | Path, manifest: dict | Manifest) -> None:
    """Write a manifest atomically: .part first, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.to_dict() if isinstance(manifest, Manifest) else manifest
    tmp = path.with_name(path.name + PART_SUFFIX)
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
