"""pdf-form-check: exit 1 when a PDF form's A1 or A2 field is "yes".

Run from source:  python -m apps.pdf_form_check form.pdf
As a command:     pdf-form-check form.pdf

The exit code is the result — this program is meant to be consumed
by another program, so it prints one short line and lets the code
speak. See README.md for the full exit-code contract.
"""

from .meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
