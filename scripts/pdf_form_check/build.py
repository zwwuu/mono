"""Build pdf-form-check.

Only PACKAGE lives here. The app name and version are read from
src/apps/pdf_form_check/meta.py by DefaultBuildConfig, and the entry
point is src/apps/pdf_form_check/__main__.py. scripts/ is not a
package, so build_config.py is loaded from the parent directory
instead of imported relatively.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_config import DefaultBuildConfig


class PdfFormCheckBuild(DefaultBuildConfig):
    PACKAGE = "apps.pdf_form_check"
    CONSOLE = True


if __name__ == "__main__":
    PdfFormCheckBuild().main()
