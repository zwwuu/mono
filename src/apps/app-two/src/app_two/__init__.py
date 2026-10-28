"""app-two: list files matching a configured pattern.

Run from source:  python -m app_two [--folder DIR] [--pattern GLOB]
As an exe:        app-two
Also reads its bundled assets/sample.txt and its bundled templates/
(monthly-summary.xlsx, letters/welcome-letter.docx) to prove
bundled-file loading works in the frozen build, including template
files with arbitrary names.
"""

from app_two.meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
