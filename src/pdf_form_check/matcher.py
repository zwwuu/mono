"""Decide whether a raw PDF field value counts as "yes".

Normalization is deliberately forgiving about presentation and
strict about content: case, surrounding whitespace, and a leading
slash (how pypdf reports a checkbox's "/Yes" state) are ignored,
but the value itself must be exactly "yes" — "yess", "yes/no",
"on", and "1" do not count.
"""


class YesMatcher:
    """Normalize a field value and test it against the yes-values."""

    def __init__(self, yes_values=("yes",)):
        self.yes_values = tuple(v.lower() for v in yes_values)

    @staticmethod
    def normalize(value):
        """A field value as a comparable string.

        None (an unfilled field) becomes "". Bytes decode as UTF-8
        with replacement. One leading "/" is stripped, because
        checkbox states arrive as PDF names ("/Yes", "/Off").
        """
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        text = str(value).strip()
        text = text.removeprefix("/")
        return text.strip().lower()

    def is_yes(self, value):
        """True when the normalized value is one of the yes-values."""
        return self.normalize(value) in self.yes_values
