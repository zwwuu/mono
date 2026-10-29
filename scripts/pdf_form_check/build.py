"""Build pdf-form-check.

APP_NAME and VERSION are imported from the app's own meta.py — the
same constants the app itself re-exports — so they are defined
once. The central build puts src/ on PYTHONPATH for this module;
a direct run needs PYTHONPATH=src (or an editable install).

Run as a module from the repo root, never as a file:
`python -m scripts.pdf_form_check.build` (or via scripts/build.py).
"""

from apps.pdf_form_check.meta import APP_NAME, __version__

from ..build_config import DefaultBuildConfig


class PdfFormCheckBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    PACKAGE = "apps.pdf_form_check"
    CONSOLE = True


if __name__ == "__main__":
    PdfFormCheckBuild().main()
