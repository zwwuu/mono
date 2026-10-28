"""Stage 3: aggregate parsed records into an index."""

__all__ = ["build_index"]


def build_index(records):
    """An index document for a list of note records."""
    return {
        "notes": records,
        "total_notes": len(records),
        "total_words": sum(r["words"] for r in records),
    }
