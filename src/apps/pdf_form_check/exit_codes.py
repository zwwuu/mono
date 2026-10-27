"""Exit codes. This is the program's contract with its caller.

0 is a clean negative (the form parsed, neither field is "yes"), not
an error. 1 is the condition the caller is checking for. 2–5 are
failures, each naming a different problem so the caller can tell
"no" apart from "couldn't check".
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """One code per outcome; see README.md for the full table."""

    NO_MATCH = 0  # parsed OK; neither checked field is "yes"
    MATCH = 1  # at least one checked field is "yes"
    USAGE = 2  # bad command line (argparse's own code)
    INPUT_ERROR = 3  # file missing, not a file, or unreadable
    MALFORMED = 4  # not a PDF, corrupted, or encrypted without a usable password
    FIELDS_MISSING = 5  # parsed OK, but a checked field is not in the form
