"""Default PyInstaller build config — subclass it per app.

Each app's build script (scripts/<package>/build.py) is a tiny subclass that
sets PACKAGE and inherits everything else: the
PyInstaller command line, the zip bundle, and the --clean /
--no-zip / --name / --distpath / --workpath flags. The app name is
not set in the script: it is read from the package's meta.py
(the same constants the app itself re-exports from its __init__.py),
so the name is defined once. Override a class attribute to customize an app:

    APP_NAME:       exe name (default: read from the package's meta.py;
                    set it only to rename the exe)
    PACKAGE:        importable package under src/ (required)
    CONSOLE:        True = console app, False = windowed GUI (default True)
    HIDDEN_IMPORTS: modules PyInstaller can't see, e.g. dynamic imports
    EXTRA_DATAS:    extra files/folders to bundle — the way to include
                    template .docx/.xlsx files or template folders with
                    arbitrary names, e.g. ("src/app_two/templates",
                    "templates"). (source, dest) pairs, source relative
                    to the repo root; directories are bundled
                    recursively. A pair means "this source ends up at
                    dest, flat next to the exe". Locate them at runtime
                    with common.utils.app_file_path().
    ICON:           path to a .ico file, relative to the repo root (or None)

Data placement: PyInstaller forces --add-data into _internal, so
the build never uses it. After PyInstaller runs, the data folders
and EXTRA_DATAS are copied flat into the output folder, next to
the exe (config/, assets/, ...). The frozen lookup is the same
shape: <parts> next to the exe. No .spec files: everything
PyInstaller needs is passed on the command line, and its
internally generated spec is pointed at the build dir with
--specpath so the repo stays spec-free.
"""

import argparse
import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile


class DefaultBuildConfig:
    """Build one app with PyInstaller and bundle the output into a zip."""
    APP_NAME = None
    VERSION = None
    ENTRY_POINT = None
    CONSOLE = True

    def __init__(self):
       pass

    def build_command(self, args):
        """The full PyInstaller command line for this app."""
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--name",
            self.APP_NAME,
        ]
        cmd.append("--console" if self.CONSOLE else "--windowed")
        cmd.append("--onedir")

        cmd.append(self.ENTRY_POINT)
        return cmd

    def main(self):
        """Parse flags, run PyInstaller, and bundle the output into a zip."""
        cmd = self.build_command()
        print(f"+ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
