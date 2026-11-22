"""Business procedures: the work the FTP server runs on a claimed file.

A procedure is a plain function::

    def my_procedure(input_path: Path, workdir: Path, ctx: dict) -> Path:
        ... read input_path, write the result into workdir ...
        return output_path

Register it, then point a job at it in the watcher config::

    register_procedure("reformat-invoices", "1.0", reformat_invoices)

Rules a procedure must follow (the watcher enforces the rest):
  * never modify the input file in place -- write a new output file;
  * the output must be non-empty and, for CSV, must parse;
  * raise on any bad input. A raise means FAILED + dead-letter, never
    a half-written publish. The watcher only publishes after the
    procedure returns AND the output verifies.

Built-ins below are examples. Add yours in ``procedures_local.py``
(next to the watcher config) and import it -- no change to the
watcher itself is needed.
"""

from __future__ import annotations

import csv
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProcedureFn = Callable[[Path, Path, dict], Path]


@dataclass(frozen=True)
class Procedure:
    name: str
    version: str
    fn: ProcedureFn


REGISTRY: dict[str, Procedure] = {}


def register_procedure(name: str, version: str, fn: ProcedureFn) -> None:
    REGISTRY[name] = Procedure(name, version, fn)


def get_procedure(name: str) -> Procedure:
    if name not in REGISTRY:
        raise ValueError(f"unknown procedure {name!r}; registered: {sorted(REGISTRY)}")
    return REGISTRY[name]


# -- CSV helpers (shared by the built-ins) ------------------------------


def _read_csv_rows(path: Path) -> list[list[str]]:
    """Parse a CSV file. Raises ValueError when it has no rows at all."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError("empty CSV: no header row")
    return rows


def _write_csv_rows(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


# -- built-in procedures ------------------------------------------------


def rename_only(input_path: Path, workdir: Path, ctx: dict) -> Path:
    """No content change; the watcher renames on publish via output_pattern."""
    out = workdir / f"PROC-{input_path.name}"
    shutil.copyfile(input_path, out)
    return out


def normalize_csv(input_path: Path, workdir: Path, ctx: dict) -> Path:
    """Strip whitespace, drop fully-empty rows, keep header + order.

    Fails loudly on a file that is not parseable CSV -- that is a
    correctness gate, not something to silently fix.
    """
    rows = _read_csv_rows(input_path)
    header = [c.strip() for c in rows[0]]
    if not any(header):
        raise ValueError("empty CSV header")
    cleaned = [header]
    for row in rows[1:]:
        cells = [c.strip() for c in row]
        if any(cells):
            cleaned.append(cells)
    out = workdir / input_path.name
    _write_csv_rows(out, cleaned)
    return out


def uppercase_headers_csv(input_path: Path, workdir: Path, ctx: dict) -> Path:
    rows = _read_csv_rows(input_path)
    rows[0] = [c.strip().upper() for c in rows[0]]
    out = workdir / input_path.name
    _write_csv_rows(out, rows)
    return out


register_procedure("rename-only", "1.0", rename_only)
register_procedure("normalize-csv", "1.0", normalize_csv)
register_procedure("uppercase-headers-csv", "1.0", uppercase_headers_csv)
