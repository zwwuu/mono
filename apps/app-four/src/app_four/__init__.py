"""app-four: find duplicate files in a folder and report them.

Run from source:  python -m app_four [--folder DIR] [--pattern GLOB]
As an exe:        app-four
Read-only by design: it never deletes anything, it just reports
duplicate groups (same size, same SHA-256) and the bytes they waste,
as a JSON manifest, plus an emailed summary when groups are found.
"""

from app_four.meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
