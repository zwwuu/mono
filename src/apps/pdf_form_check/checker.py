"""Orchestrate one check: read the form, resolve fields, match "yes".

Precedence matters when a form is half-usable: a match in any
present field wins over a missing field, because "A1 or A2 is yes"
is already true. Only when nothing matches and something is missing
does the check fail with FieldsMissingError — the caller must not
read that as "no".
"""

from dataclasses import dataclass

from .errors import FieldsMissingError
from .field_resolver import FieldResolver
from .form_reader import PdfFormReader
from .matcher import YesMatcher

DEFAULT_FIELDS = ("A1", "A2")


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check.

    values maps every checked field name to its raw value, or None
    when the field was not found. matched_fields and missing_fields
    are subsets of the checked names.
    """

    matched: bool
    values: dict
    matched_fields: tuple = ()
    missing_fields: tuple = ()


class FormChecker:
    """Check a PDF form's fields for "yes" values."""

    def __init__(self, fields=DEFAULT_FIELDS, matcher=None):
        self.fields = tuple(fields)
        self.matcher = matcher or YesMatcher()

    def check(self, path):
        """Check the form at `path`; raises on input/parse/missing errors."""
        raw_fields = PdfFormReader(path).read_fields()
        resolver = FieldResolver(raw_fields)
        values = {}
        matched = []
        missing = []
        for wanted in self.fields:
            actual = resolver.resolve(wanted)
            if actual is None:
                missing.append(wanted)
                values[wanted] = None
            else:
                value = raw_fields[actual]
                values[wanted] = value
                if self.matcher.is_yes(value):
                    matched.append(wanted)
        if matched:
            return CheckResult(True, values, tuple(matched), tuple(missing))
        if missing:
            raise FieldsMissingError(
                f"required field(s) not found: {', '.join(missing)}",
                missing=tuple(missing),
            )
        return CheckResult(False, values)
