"""Business procedures for the PDF report job.

`rename-pdf-report`: the portal's downloads are named
``Monthly-Report-<VENDOR>-<YYYYMMDD>.pdf``; the business name is
``<VENDOR>_<YYYYMMDD>_monthly-report.pdf``. The procedure verifies
the %PDF magic first -- a bad download fails loud instead of
shipping -- then parses the vendor and report date out of the
filename and writes the renamed copy into the workdir. The agent
applies ``rename_pattern`` on top; the sample configs set it to
``{name}{ext}`` so the procedure's name passes through unchanged.

A procedure is (input_path, workdir, ctx) -> output_path:
  * read input_path, write a NEW file into workdir, return it;
  * never edit the input in place;
  * raise on bad input -- a raise is FAILED, never a half-written
    upload.
"""

import re
from pathlib import Path

from fileflow_agent.domain.procedures import register_procedure

_NAME_RE = re.compile(r"^Monthly-Report-([A-Za-z0-9_-]+)-(\d{8})\.pdf$")


def rename_pdf_report(input_path: Path, workdir: Path, ctx: dict) -> Path:
    data = input_path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"not a PDF: {input_path.name}")
    m = _NAME_RE.match(input_path.name)
    if not m:
        raise ValueError(
            f"unexpected report filename: {input_path.name!r} "
            "(want Monthly-Report-<VENDOR>-<YYYYMMDD>.pdf)"
        )
    vendor, yyyymmdd = m.groups()
    out = workdir / f"{vendor}_{yyyymmdd}_monthly-report.pdf"
    out.write_bytes(data)
    return out


register_procedure("rename-pdf-report", "1.0", rename_pdf_report)
