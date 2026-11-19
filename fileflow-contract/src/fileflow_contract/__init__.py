"""FileFlow manifest contract -- the shared transfer format.

One package, stdlib only, so any project can depend on it: the
agent, the watcher, and whatever comes next. The contract is three
things:

- ``manifest``: the manifest document itself -- ``MANIFEST_VERSION``,
  ``Manifest`` / ``ManifestFile``, and atomic read/write;
- ``identity``: run identity -- ``new_run_id``, ``idempotency_key``,
  ``utcnow_iso``;
- ``verify``: SHA-256 integrity -- ``sha256_file``, ``file_entry``,
  ``check_manifest``, ``verify_files``.

The manifest is a JSON document listing the transferred files with
their SHA-256 + size, plus the run/job identity. ``extra`` carries
per-flow fields (source_url, procedure, direction, ...) so the core
stays stable while either side can attach provenance without a
schema change.

Identity: the idempotency key is ``job|run_id``. The run_id IS the
run's identity -- it is random per run, so the key is unique per
run without any content hash. A combined hash of the file set
would add nothing: it is opaque (it names no file),
all-or-nothing (one changed byte re-identifies the whole batch),
and unverifiable (no single file on disk hashes to it).
Per-file identity lives where it is actually checked: each file's
own sha256 in the manifest's ``files[]``.

Change the format in one place, change it here, and bump
``MANIFEST_VERSION``. Consumers pin ``fileflow-contract>=1.0``.
"""

from __future__ import annotations

from .identity import idempotency_key, new_run_id, utcnow_iso
from .manifest import MANIFEST_VERSION, Manifest, ManifestFile, read_manifest, write_manifest
from .verify import check_manifest, file_entry, sha256_file, verify_files

__version__ = "1.0.0"

__all__ = [
    "MANIFEST_VERSION",
    "Manifest",
    "ManifestFile",
    "check_manifest",
    "file_entry",
    "idempotency_key",
    "new_run_id",
    "read_manifest",
    "sha256_file",
    "utcnow_iso",
    "verify_files",
    "write_manifest",
]
