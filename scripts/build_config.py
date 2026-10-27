"""Default PyInstaller build config — subclass it per app.

Each app's build script (apps/<app>/build.py) is a tiny subclass that
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
                    to the app folder; directories are bundled
                    recursively. A pair means "this source ends up at
                    dest, flat next to the exe". Locate them at runtime
                    with common.utils.app_file_path().
    ICON:           path to a .ico file, relative to the app folder (or None)

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
    PACKAGE = None

    # Folders inside the app package, placed flat next to the exe
    # when they exist. "config" and "assets" are just the defaults —
    # folder names are arbitrary per app, so override this to use any
    # names, e.g. DATA_FOLDERS = ("settings", "resources").
    DATA_FOLDERS = ("config", "assets")

    CONSOLE = True
    HIDDEN_IMPORTS = ()
    EXTRA_DATAS = ()
    ICON = None

    # The loaded meta module, cached so a build executes it once
    # no matter how many attributes are read from it.
    _meta_module = None

    def __init__(self):
        if not self.PACKAGE:
            raise TypeError(f"{type(self).__name__} must set PACKAGE")
        # This file lives in scripts/, so its parent is the repo root.
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Each app lives in its own folder, apps/<app>/, named after the
        # app (package with underscores turned into hyphens).
        self.app_dir = os.path.join(self.root, "apps", self.PACKAGE.replace("_", "-"))
        self.src_dir = os.path.join(self.app_dir, "src")
        self.package_dir = os.path.join(self.src_dir, self.PACKAGE)
        if self.APP_NAME is None:
            # The app's meta.py APP_NAME is the single source of truth; a
            # subclass only sets APP_NAME to rename the exe.
            self.APP_NAME = self.read_app_name()
        if not self.APP_NAME:
            raise TypeError(f"{type(self).__name__} must set APP_NAME")

    def entry_point(self):
        """The app's __main__.py."""
        return os.path.join(self.package_dir, "__main__.py")

    def copy_data_files(self, dist_dir):
        """Copy the app's data flat into dist_dir, next to the exe.

        Builds skip --add-data (PyInstaller would force the files
        into _internal), so this places them after the build
        instead: DATA_FOLDERS as <folder>/, EXTRA_DATAS at their dest.
        A missing source fails the build rather than shipping an app
        without its data. Directories are replaced wholesale so a
        rebuild never leaves stale files behind.
        """
        targets = []
        for folder in self.DATA_FOLDERS:
            source = os.path.join(self.package_dir, folder)
            if os.path.isdir(source):
                targets.append((source, folder))
        for source, dest in self.EXTRA_DATAS:
            targets.append((os.path.join(self.app_dir, source), dest))
        for source, dest in targets:
            if not os.path.exists(source):
                raise SystemExit(f"data file not found: {source}")
            target = os.path.join(dist_dir, dest)
            if os.path.isdir(source):
                if os.path.islink(target) or (os.path.exists(target) and not os.path.isdir(target)):
                    os.unlink(target)
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(source, target)
            else:
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                shutil.copy2(source, target)

    def _load_meta_module(self):
        """Load the package's meta.py as a module, by file path.

        Loaded once per build object and cached. This executes the
        module's top level, but meta.py holds constants only, so
        there is nothing to import and no side effect — and it does
        not require the package itself to be installed.
        """
        if self._meta_module is None:
            meta_path = os.path.join(self.package_dir, "meta.py")
            if not os.path.isfile(meta_path):
                raise SystemExit(f"meta.py not found: {meta_path}")
            spec = importlib.util.spec_from_file_location(
                self.PACKAGE, meta_path, submodule_search_locations=[self.package_dir]
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._meta_module = (module, meta_path)
        return self._meta_module

    def read_version(self):
        """The app's __version__ from its meta.py."""
        module, meta_path = self._load_meta_module()
        version = getattr(module, "__version__", None)
        if not isinstance(version, str):
            raise SystemExit(f"__version__ not found in {meta_path}")
        return version

    def read_app_name(self):
        """The app's APP_NAME from its meta.py."""
        module, meta_path = self._load_meta_module()
        name = getattr(module, "APP_NAME", None)
        if not isinstance(name, str):
            raise SystemExit(f"APP_NAME not found in {meta_path}")
        return name

    @staticmethod
    def bundle_to_zip(output_path, zip_path):
        """Zip a build output dir, entries rooted at its folder name."""
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _dirnames, filenames in os.walk(output_path):
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    zf.write(full, os.path.relpath(full, os.path.dirname(output_path)))

    def parse_args(self, argv=None):
        """The flags every app build accepts."""
        parser = argparse.ArgumentParser(description=f"Build {self.APP_NAME} with PyInstaller.")
        parser.add_argument(
            "--clean", action="store_true", help="wipe PyInstaller's build cache first"
        )
        parser.add_argument("--no-zip", action="store_true", help="skip the zip bundle")
        parser.add_argument("--name", default=self.APP_NAME, help="exe name (default: %(default)s)")
        parser.add_argument(
            "--distpath", default=os.path.join(self.root, "dist"), help="output directory"
        )
        parser.add_argument(
            "--workpath", default=os.path.join(self.root, "build"), help="build cache directory"
        )
        return parser.parse_args(argv)

    def build_command(self, args):
        """The full PyInstaller command line for this app."""
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--name",
            args.name,
            "--paths",
            self.src_dir,
        ]
        cmd.append("--console" if self.CONSOLE else "--windowed")
        for module in self.HIDDEN_IMPORTS:
            cmd += ["--hidden-import", module]
        if self.ICON:
            cmd += ["--icon", os.path.join(self.app_dir, self.ICON)]
        cmd += [
            "--distpath",
            args.distpath,
            "--workpath",
            args.workpath,
            "--specpath",
            args.workpath,
            "--noconfirm",
        ]
        cmd.append("--onedir")
        if args.clean:
            cmd.append("--clean")
        cmd.append(self.entry_point())
        return cmd

    def main(self, argv=None):
        """Parse flags, run PyInstaller, and bundle the output into a zip."""
        args = self.parse_args(argv)
        if importlib.util.find_spec("PyInstaller") is None:
            raise SystemExit("PyInstaller is not installed: pip install pyinstaller")
        entry = self.entry_point()
        if not os.path.isfile(entry):
            raise SystemExit(f"entry point not found: {entry}")
        cmd = self.build_command(args)
        print(f"+ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        output_path = os.path.join(args.distpath, args.name)
        self.copy_data_files(output_path)
        if args.no_zip:
            print(f"done -> {output_path}")
        else:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            zip_path = os.path.join(
                args.distpath, f"{args.name}-{self.read_version()}-{timestamp}.zip"
            )
            self.bundle_to_zip(output_path, zip_path)
            print(f"done -> {zip_path} (unzipped output in {output_path})")
