# pdf-form-check

Check a PDF form for "yes" in fields A1/A2 and report the result via
exit code. This program is meant to be consumed by another program:
the exit code is the result, stdout carries one short line, and
errors go to stderr.

```bash
pip install -e .
pdf-form-check form.pdf; echo $?
# MATCH: A1=yes   (exit 1)

python -m pdf_form_check form.pdf   # same, without installing the command
```

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | `NO_MATCH` — the form parsed, neither checked field is "yes" |
| 1 | `MATCH` — at least one checked field is "yes" |
| 2 | `USAGE` — bad command line (missing file argument, unknown flag) |
| 3 | `INPUT_ERROR` — file missing, not a file, or unreadable |
| 4 | `MALFORMED` — not a PDF, corrupted/truncated, empty, or encrypted |
| 5 | `FIELDS_MISSING` — the PDF parsed, but a checked field is not in the form |

0 is a clean negative, not an error. The failure codes exist so the
caller can tell "no" apart from "couldn't check": a missing field or
a corrupt file must never silently read as "no".

## Usage

```bash
pdf-form-check form.pdf [--fields A1 A2] [--quiet]
```

- `--fields` overrides the checked fields (default: `A1 A2`). Checking
  other fields works the same way: `pdf-form-check form.pdf --fields B7`.
- `--quiet` prints nothing on success; the exit code is the result
  either way. Errors still go to stderr.

Output format:

```
MATCH: A1=yes
NO_MATCH: A1=empty, A2=no
pdf-form-check: error: required field(s) not found: A2
```

## What counts as "yes"

The raw field value is normalized, then compared exactly:

- Case-insensitive: `yes`, `Yes`, `YES` all count.
- Surrounding whitespace is ignored: `"  yes  "` counts.
- A leading slash is stripped: a checked checkbox reports its state
  as `/Yes` in pypdf, and that counts. An unchecked checkbox (`/Off`)
  does not.
- Anything else does not count: `yess`, `yes/no`, `on`, `1`, empty.

## Field names

PDF field names are hierarchical and case-sensitive, but real forms
are inconsistent, so lookup tries in order: exact (`A1`), last
segment (`Section.A1` matches `A1`; a bare `XA1` does not),
case-insensitive exact (`a1`), case-insensitive last segment.
When several names match at one step, the alphabetically first wins.

## Edge cases

| Situation | Result |
| --------- | ------ |
| Fields exist but are empty (form not filled) | 0 — unfilled is "no", not an error |
| A1 is "yes", A2 is missing | 1 — a match wins over a missing field |
| A1 is "no", A2 is missing | 5 — without a match, the check cannot prove "no" |
| PDF has no form fields at all | 5 |
| File missing, a directory, or unreadable | 3 |
| Not a PDF, truncated, corrupted, or empty file | 4 |
| Encrypted PDF | 4 — an empty password is tried; anything still locked is unreadable, since this program takes no password input |
| Signed PDF | Parses normally; the signature field is ignored. Signature validity is NOT checked — that needs a dedicated validator (pyHanko, endesive) |

## Layout

```
src/pdf_form_check/
├── exit_codes.py      # the exit-code contract
├── errors.py          # one exception type per failure code
├── matcher.py         # normalize a value, test for "yes"
├── field_resolver.py  # wanted name -> actual field name
├── form_reader.py     # open a PDF, return its fields, map failures
├── checker.py         # orchestrate: read, resolve, match
└── cli.py             # arguments, output, exit code
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
ruff format --check src tests
```
