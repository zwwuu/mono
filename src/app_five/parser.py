"""Stage 2: read one note into an index record."""

import re

__all__ = ["parse_note"]

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def parse_note(path):
    """Title, links, and word count for one Markdown file.

    The title is the first `# ` heading, falling back to the file
    stem when the note has none. Links are the Markdown
    `[text](url)` pairs, in order of appearance.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    title = path.stem
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "file": path.name,
        "title": title,
        "links": [{"text": t, "url": u} for t, u in _LINK_RE.findall(text)],
        "words": len(text.split()),
    }
