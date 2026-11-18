"""Your business procedures go here -- example only.

Copy this file next to the watcher config, add your own procedures,
and set "procedures_module": "procedures_local" in watcher.json.
The watcher imports it on start; no change to the watcher itself is needed.

A procedure: (input_path, workdir, ctx) -> output_path.
  * read input_path, write a NEW file into workdir, return it;
  * never edit the input in place;
  * raise on bad input -- a raise is FAILED + dead-letter, never a
    half-written publish.
"""
from pathlib import Path

from fileflow_watcher.domain.procedures import register_procedure


def reformat_invoices(input_path: Path, workdir: Path, ctx: dict) -> Path:
    # Example: rename + reformat. Replace this body with the real
    # business rules for the job (parse, validate, reformat, ...).
    out = workdir / f"INV-{input_path.name}"
    text = input_path.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("invoice file is empty")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


register_procedure("reformat-invoices", "1.0", reformat_invoices)
