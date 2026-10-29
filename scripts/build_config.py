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
    PACKAGE:        importable package under src/ (required), as a dotted
                    path, e.g. "apps.app_one" -> src/apps/app_one/.
                    A single-component name also works ("fake_app" ->
                    src/fake_app/), which is what the build tests use.
    CONSOLE:        True = console app, False = windowed GUI (default True)
    EXTRA_DATAS:    extra files/folders to bundle — the way to include
                    template .docx/.xlsx files or template folders with
                    arbitrary names, e.g. ("src/apps/app_two/templates",
                    "templates"). (source, dest) pairs, source relative
                    to the repo root; directories are bundled
                    recursively. A pair means "this source ends up at
                    dest, flat next to the exe". Locate them at runtime
                    with common.utils.app_file_path().

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
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


class DefaultBuildConfig:
    """Build one app with PyInstaller and bundle the output into a zip."""

    APP_NAME = None
    VERSION = None
    PACKAGE = None
    CONSOLE = True
    EXTRA_DATAS = ()

    def __init__(self):
        # Root is derived from this file's location (scripts/build_config.py),
        # not from the per-app script that imports it, so the same class
        # works whether it is run from scripts/<app>/build.py or loaded
        # in a test with a patched __file__.
        self.root = str(Path(__file__).resolve().parents[1])
        self.src_dir = str(Path(self.root) / "src")
        if self.PACKAGE:
            self.package_dir = str(Path(self.src_dir).joinpath(*self.PACKAGE.split(".")))
        else:
            self.package_dir = None
        self._meta_module = None
        self._meta_path = None
        # App name defaults to meta.py; an explicit APP_NAME wins.
        if self.APP_NAME is None:
            module, _ = self._load_meta_module()
            app_name = getattr(module, "APP_NAME", None)
            if app_name is None:
                raise SystemExit("APP_NAME not found in meta.py")
            self.APP_NAME = app_name

    # -- meta.py ---------------------------------------------------------

    def _load_meta_module(self):
        """Load the package's meta.py once (it holds constants only).

        Returns (module, meta_path). Importing — rather than parsing —
        the file also resolves computed values such as
        APP_NAME = "fake" + "-app", and annotated assignments such as
        __version__: str = "9.9.9". A missing file or a value that was
        only annotated, never assigned, is a build error, not a guess.
        """
        if self._meta_module is not None:
            return self._meta_module, self._meta_path
        if not self.package_dir:
            raise SystemExit("meta.py not found: PACKAGE is not set")
        meta_path = Path(self.package_dir) / "meta.py"
        if not meta_path.is_file():
            raise SystemExit(f"meta.py not found: {meta_path}")
        spec = importlib.util.spec_from_file_location("app_meta", meta_path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"meta.py not found: {meta_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._meta_module = module
        self._meta_path = str(meta_path)
        return module, self._meta_path

    def read_version(self):
        """The app version from meta.py (or the explicit VERSION)."""
        if self.VERSION:
            return self.VERSION
        module, _ = self._load_meta_module()
        version = getattr(module, "__version__", None)
        if version is None:
            raise SystemExit("__version__ not found in meta.py")
        return version

    # -- arguments -------------------------------------------------------

    def parse_args(self, argv=None):
        """Flags for one app's build script."""
        parser = argparse.ArgumentParser(description="Build one app with PyInstaller.")
        parser.add_argument("--clean", action="store_true", help="clean before building")
        parser.add_argument("--no-zip", action="store_true", help="skip the zip bundle")
        parser.add_argument("--name", default=None, help="override the exe name")
        parser.add_argument("--distpath", default=None, help="output dir (default: <root>/dist)")
        parser.add_argument("--workpath", default=None, help="build dir (default: <root>/build)")
        return parser.parse_args(argv)

    # -- PyInstaller -----------------------------------------------------

    def entry_point(self):
        """The file PyInstaller builds: the package's __main__.py."""
        if not self.package_dir:
            raise SystemExit("cannot build: PACKAGE is not set")
        entry = Path(self.package_dir) / "__main__.py"
        if not entry.is_file():
            raise SystemExit(f"entry point not found: {entry}")
        return str(entry)

    def build_command(self, args=None):
        """The full PyInstaller command line for this app."""
        if args is None:
            args = self.parse_args([])
        name = args.name or self.APP_NAME
        distpath = args.distpath or str(Path(self.root) / "dist")
        workpath = args.workpath or str(Path(self.root) / "build")
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--name",
            name,
        ]
        cmd.append("--console" if self.CONSOLE else "--windowed")
        cmd.append("--onedir")
        cmd.extend(["--paths", str(self.src_dir)])
        cmd.extend(["--distpath", distpath])
        cmd.extend(["--workpath", workpath])
        # Keep the generated .spec out of the repo.
        cmd.extend(["--specpath", workpath])
        if args.clean:
            cmd.append("--clean")
        # Never --add-data: data is copied flat next to the exe after
        # PyInstaller runs (see copy_data_files), because --add-data
        # would bury it in _internal instead.
        cmd.append(self.entry_point())
        return cmd

    # -- data files and zip ----------------------------------------------

    def copy_data_files(self, out_dir):
        """Copy data folders and EXTRA_DATAS flat next to the exe.

        Data folders are replaced wholesale, so a stale file left in
        the output folder from an earlier build does not survive.
        A missing data folder is skipped silently; a missing
        EXTRA_DATAS source is an error.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # The app's data folders, copied as-is. Not a knob: every app
        # keeps its config in config/ and its templates in assets/.
        for folder in ("config", "assets"):
            src_folder = Path(self.package_dir) / folder
            if not src_folder.is_dir():
                continue
            dest_folder = out / folder
            if dest_folder.is_symlink() or dest_folder.is_file():
                dest_folder.unlink()
            elif dest_folder.is_dir():
                shutil.rmtree(dest_folder)
            shutil.copytree(src_folder, dest_folder)
        for src_rel, dest_rel in self.EXTRA_DATAS:
            src_path = Path(self.root) / src_rel
            if not src_path.exists():
                raise SystemExit(f"extra data not found: {src_path}")
            dest_path = out / dest_rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                if dest_path.is_symlink() or dest_path.is_file():
                    dest_path.unlink()
                elif dest_path.is_dir():
                    shutil.rmtree(dest_path)
                shutil.copytree(src_path, dest_path)
            else:
                if dest_path.is_symlink() or dest_path.is_file():
                    dest_path.unlink()
                elif dest_path.is_dir():
                    shutil.rmtree(dest_path)
                shutil.copy2(src_path, dest_path)

    def bundle_to_zip(self, out_dir, zip_path):
        """Zip an output folder, rooted at the folder's own name."""
        out = Path(out_dir)
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    zf.write(path, out.name / path.relative_to(out))
        return str(zip_path)

    # -- main ------------------------------------------------------------

    def main(self, argv=None):
        """Parse flags, run PyInstaller, and bundle the output into a zip."""
        args = self.parse_args(argv)
        if args.name:
            self.APP_NAME = args.name
        version = self.read_version()
        distpath = Path(args.distpath or Path(self.root) / "dist")
        workpath = Path(args.workpath or Path(self.root) / "build")
        distpath.mkdir(parents=True, exist_ok=True)
        workpath.mkdir(parents=True, exist_ok=True)
        out_dir = distpath / self.APP_NAME
        if args.clean and out_dir.exists():
            shutil.rmtree(out_dir)
        cmd = self.build_command(args)
        print(f"+ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        if not out_dir.is_dir():
            raise SystemExit(f"PyInstaller did not produce {out_dir}")
        self.copy_data_files(str(out_dir))
        if args.no_zip:
            print(f"done -> {out_dir}")
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = distpath / f"{self.APP_NAME}-{version}-{timestamp}.zip"
        self.bundle_to_zip(str(out_dir), str(zip_path))
        print(f"done -> {zip_path}")
