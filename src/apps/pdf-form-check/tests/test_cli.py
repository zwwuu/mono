"""CLI tests: real subprocess runs, asserting exit codes and output."""

import subprocess
import sys

from tests.pdf_factory import make_form_pdf


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "pdf_form_check", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_match_exit_1(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes", "A2": "no"})
    proc = run_cli(str(pdf))
    assert proc.returncode == 1
    assert proc.stdout.strip() == "MATCH: A1=yes"


def test_cli_no_match_exit_0(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": None, "A2": "no"})
    proc = run_cli(str(pdf))
    assert proc.returncode == 0
    assert proc.stdout.strip() == "NO_MATCH: A1=empty, A2=no"


def test_cli_quiet_suppresses_stdout(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes", "A2": "no"})
    proc = run_cli("--quiet", str(pdf))
    assert proc.returncode == 1
    assert proc.stdout == ""


def test_cli_fields_override(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes", "A2": "no"})
    # --fields is greedy (nargs="+"), so the pdf path comes first.
    proc = run_cli(str(pdf), "--fields", "A2")
    assert proc.returncode == 0


def test_cli_missing_fields_exit_5(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "no"})
    proc = run_cli(str(pdf))
    assert proc.returncode == 5
    assert "required field" in proc.stderr


def test_cli_malformed_exit_4(tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"not a pdf")
    proc = run_cli(str(pdf))
    assert proc.returncode == 4
    # Exactly one stderr line: pypdf's own parse warnings are silenced.
    assert proc.stderr.strip().startswith("pdf-form-check: error: cannot parse PDF")
    assert proc.stderr.strip().count("\n") == 0


def test_cli_missing_file_exit_3(tmp_path):
    proc = run_cli(str(tmp_path / "nope.pdf"))
    assert proc.returncode == 3


def test_cli_no_args_exit_2():
    proc = run_cli()
    assert proc.returncode == 2


def test_cli_version_exit_0():
    proc = run_cli("--version")
    assert proc.returncode == 0
    assert "pdf-form-check 0.1.0" in proc.stdout
