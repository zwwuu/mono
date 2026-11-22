# fileflow-contract

The FileFlow **manifest contract** as a standalone library: the
transfer manifest format, the run idempotency key, and SHA-256
verification. Stdlib only — no dependencies — so any project can
depend on it.

## What it holds

| Module | Contents |
| --- | --- |
| `fileflow_contract.manifest` | `MANIFEST_VERSION`, `Manifest`, `ManifestFile`, atomic `read_manifest` / `write_manifest` |
| `fileflow_contract.identity` | `new_run_id`, `idempotency_key`, `utcnow_iso` |
| `fileflow_contract.verify` | `sha256_file`, `file_entry`, `check_manifest`, `verify_files` |
| `fileflow_contract.tracking` | `file_already_recorded` — layer-2 idempotency: has this exact file (job + name + hash) already been recorded in a completed run? |

Everything is also re-exported from `fileflow_contract` itself:

```python
from fileflow_contract import Manifest, ManifestFile, idempotency_key, verify_files
```

## The contract

A manifest is a JSON document listing the transferred files with
their SHA-256 + size, plus the run/job identity. `extra` carries
per-flow fields (source_url, procedure, direction, ...) so the core
stays stable while either side can attach provenance without a
schema change.

The idempotency key is `job|run_id`. The run_id IS the run's
identity — it is random per run, so the key is unique per run
without any content hash. Per-file identity lives where it is
actually checked: each file's own sha256 in the manifest's
`files[]`.

Change the format in one place, change it here, and bump
`MANIFEST_VERSION`.

## Install

```bash
pip install -e .          # from this directory
```

Or depend on it from another project:

```toml
dependencies = ["fileflow-contract>=1.0"]
```

## Tests

```bash
python -m pytest tests/ -q
python -m ruff check src tests
python -m ruff format --check src tests
```

## Used by

- `fileflow-agent` — seals manifests, uploads them
- `fileflow-watcher` — verifies manifests, publishes, stages outbound batches
