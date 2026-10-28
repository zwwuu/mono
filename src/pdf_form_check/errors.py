"""Errors, each carrying the exit code the CLI should return."""

from pdf_form_check.exit_codes import ExitCode


class FormCheckError(Exception):
    """Base class: every anticipated failure maps to one exit code."""

    exit_code = ExitCode.MALFORMED


class InputError(FormCheckError):
    """The input path cannot be used at all."""

    exit_code = ExitCode.INPUT_ERROR


class MalformedPdfError(FormCheckError):
    """The file is not a readable PDF."""

    exit_code = ExitCode.MALFORMED


class FieldsMissingError(FormCheckError):
    """The PDF parsed, but a checked field is not in the form."""

    exit_code = ExitCode.FIELDS_MISSING

    def __init__(self, message, missing=()):
        super().__init__(message)
        self.missing = tuple(missing)
