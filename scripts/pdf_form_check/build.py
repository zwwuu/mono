"""Build pdf-form-check.

Only PACKAGE lives here. The app name and version are read from
src/apps/pdf_form_check/meta.py by DefaultBuildConfig, and the entry
point is src/apps/pdf_form_check/__main__.py.

Run as a module from the repo root, never as a file:
`python -m scripts.pdf_form_check.build` (or via scripts/build.py).
That is what makes the relative import below work — scripts/ is a
package, so no sys.path manipulation is needed.
"""

from ..build_config import DefaultBuildConfig


class PdfFormCheckBuild(DefaultBuildConfig):
    PACKAGE = "apps.pdf_form_check"
    CONSOLE = True


if __name__ == "__main__":
    PdfFormCheckBuild().main()
