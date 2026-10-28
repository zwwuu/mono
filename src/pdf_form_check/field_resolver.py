"""Resolve a wanted field name against a PDF's actual field names.

PDF field names are hierarchical ("Section.A1") and case-sensitive,
but real forms are inconsistent, so resolution tries in order:

1. Exact match ("A1").
2. Last-segment match ("Section.A1" resolves "A1"). A bare
   "XA1" does not match — the dot is required.
3. Case-insensitive exact match ("a1" resolves "A1").
4. Case-insensitive last-segment match.

When several names match at one step, the alphabetically first wins,
so resolution is deterministic. Returns the actual field name, or
None when nothing matches.
"""


class FieldResolver:
    """Find a wanted field in a form's field dict, forgivingly."""

    def __init__(self, fields):
        self._fields = dict(fields) if fields else {}

    def resolve(self, wanted):
        """The actual field name for `wanted`, or None."""
        if wanted in self._fields:
            return wanted
        suffix = "." + wanted
        candidates = sorted(name for name in self._fields if name.endswith(suffix))
        if candidates:
            return candidates[0]
        lowered = wanted.lower()
        candidates = sorted(name for name in self._fields if name.lower() == lowered)
        if candidates:
            return candidates[0]
        suffix = "." + lowered
        candidates = sorted(name for name in self._fields if name.lower().endswith(suffix))
        if candidates:
            return candidates[0]
        return None
