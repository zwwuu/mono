"""Read a PDF's form fields, mapping every failure to an error type.

The reader distinguishes three situations the caller must not
confuse: the path cannot be used (InputError), the file is not a
readable PDF (MalformedPdfError), and the PDF is fine but has no
form fields (an empty dict — the checker turns that into
FieldsMissingError). An encrypted PDF is tried with an empty
password; anything still locked is malformed for our purposes,
because this program takes no password input.
"""

import os

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .errors import InputError, MalformedPdfError


class PdfFormReader:
    """Open a PDF and return its AcroForm fields as a dict."""

    def __init__(self, path):
        self.path = os.fspath(path)

    def read_fields(self):
        """Field name -> raw value, or {} when the PDF has no form."""
        path = self.path
        if not os.path.exists(path):
            raise InputError(f"file not found: {path}")
        if not os.path.isfile(path):
            raise InputError(f"not a file: {path}")
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            raise InputError(f"cannot access {path}: {exc}") from exc
        if size == 0:
            raise MalformedPdfError(f"empty file: {path}")
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception as exc:
                    raise MalformedPdfError(f"encrypted PDF, password required: {path}") from exc
                if unlocked == 0:
                    raise MalformedPdfError(f"encrypted PDF, password required: {path}")
            fields = reader.get_fields()
        except (InputError, MalformedPdfError):
            raise
        except PdfReadError as exc:
            raise MalformedPdfError(f"cannot parse PDF {path}: {exc}") from exc
        except OSError as exc:
            raise InputError(f"cannot read {path}: {exc}") from exc
        except Exception as exc:
            raise MalformedPdfError(f"cannot parse PDF {path}: {exc}") from exc
        if not fields:
            return {}
        # pypdf returns one dictionary per field, not the value itself;
        # unwrap /V (absent when the field is unfilled).
        return {
            name: (field.get("/V") if isinstance(field, dict) else field)
            for name, field in fields.items()
        }
