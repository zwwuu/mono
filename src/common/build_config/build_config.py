import argparse
import datetime
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Repo root: this file is src/common/build_config/build_config.py.
ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"


class DefaultBuildConfig:
    """Build one app with PyInstaller and bundle the output into a zip.

    Subclass in src/apps/<package>/build.py, setting APP_NAME and
    VERSION from the app's meta.py plus any build options:

        from apps.my_app.meta import APP_NAME, __version__
        from common.build_config.build_config import DefaultBuildConfig


        class MyAppBuild(DefaultBuildConfig):
            APP_NAME = APP_NAME
            VERSION = __version__


        if __name__ == "__main__":
            MyAppBuild().main()

    Run from the repo root: `python -m apps.<package>.build`
    (the root build.py does this for every app). PyInstaller uses
    its default dist/build locations (./dist, ./build), so run
    from the repo root. The app must be importable — either
    `pip install -e .` or `PYTHONPATH=src` (the root build.py sets
    the latter for its subprocesses).
    """

    APP_NAME = None
    VERSION = None
    CONSOLE = True
    EXTRA_DATAS = ()

    def __init__(self):
        self.root = str(ROOT)
        # The per-app build.py lives next to the app's __main__.py.
        mod_file = sys.modules[self.__class__.__module__].__file__
        self.package_dir = str(Path(mod_file).resolve().parent)

    # -- args ------------------------------------------------------------

    def parse_args(self, argv=None):
        """Flags for the build."""
        parser = argparse.ArgumentParser(
            description=f"Build {self.APP_NAME or 'app'} with PyInstaller."
        )
        parser.add_argument("--no-zip", action="store_true", help="skip the zip bundle")
        return parser.parse_args(argv)

    # -- PyInstaller -----------------------------------------------------

    def entry_point(self):
        """The file PyInstaller builds: the package's __main__.py."""
        entry = Path(self.package_dir) / "__main__.py"
        if not entry.is_file():
            raise SystemExit(f"entry point not found: {entry}")
        return str(entry)

    def build_command(self):
        """The full PyInstaller command line for this app."""
        if not self.APP_NAME:
            raise SystemExit("APP_NAME not set in build script (import it from the app's meta.py)")
        cmd = [sys.executable, "-m", "PyInstaller", "--name", self.APP_NAME]
        cmd.append("--console" if self.CONSOLE else "--windowed")
        cmd.append("--onedir")
        # Never --add-data: data is copied flat next to the exe after
        # PyInstaller runs (see copy_data_files), because --add-data
        # would bury it in _internal instead.
        cmd.append(self.entry_point())
        return cmd

    def check_missing_modules(self, name, workpath):
        """Fail the build if PyInstaller could not resolve a hard dependency.

        PyInstaller logs every unresolvable import to
        build/<name>/warn-<name>.txt as "missing module named X -
        imported by Y (qualifiers)". Only entries imported by this
        repo's own packages fail the build: a hard top-level import in
        our code crashes the frozen exe on startup, while misses
        inside third-party packages are the library's business —
        libraries routinely tolerate them (pypdf without its optional
        Crypto/brotli extras, platform-specific modules), and a
        genuinely hard third-party dependency would already break
        `pip install -e .` in this same environment. Entries marked
        "optional" or "conditional" (try/except ImportError, platform
        checks) never fail. If the warn file isn't where expected (a
        future PyInstaller layout), the check is skipped rather than
        blocking the build.
        """
        warn_path = Path(workpath) / name / f"warn-{name}.txt"
        if not warn_path.is_file():
            return
        ours = tuple(
            d.name for d in SRC_DIR.iterdir() if d.is_dir() and (d / "__init__.py").is_file()
        )
        problems = []
        for line in warn_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("missing module named "):
                continue
            importers = line.split(" - imported by ", 1)[-1].split(", ")
            for importer in importers:
                mod, _, qualifiers = importer.partition(" (")
                mod = mod.strip().strip("'\"")
                if (
                    any(mod == pkg or mod.startswith(pkg + ".") for pkg in ours)
                    and "optional" not in qualifiers
                    and "conditional" not in qualifiers
                ):
                    problems.append(line)
                    break
        if problems:
            details = "\n".join(f"  {line}" for line in problems)
            raise SystemExit(
                f"PyInstaller could not resolve {len(problems)} hard import(s) for {name}; "
                f"install the app's dependencies first (`pip install -e .`):\n{details}"
            )

    # -- data files and zip ----------------------------------------------

    def copy_data_files(self, out_dir):
        """Copy EXTRA_DATAS flat next to the exe.

        A missing source is an error.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for src_rel, dest_rel in self.EXTRA_DATAS:
            src_path = Path(self.root) / src_rel
            if not src_path.exists():
                raise SystemExit(f"extra data not found: {src_path}")
            dest_path = out / dest_rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.is_symlink() or dest_path.is_file():
                dest_path.unlink()
            elif dest_path.is_dir():
                shutil.rmtree(dest_path)
            if src_path.is_dir():
                shutil.copytree(src_path, dest_path)
            else:
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
        args = self.parse_args(argv)
        name = self.APP_NAME
        if not name:
            raise SystemExit("APP_NAME not set in build script (import it from the app's meta.py)")
        version = self.VERSION
        if not version:
            raise SystemExit("VERSION not set in build script (import it from the app's meta.py)")
        # PyInstaller defaults: ./dist and ./build under the cwd —
        # run from the repo root.
        distpath = Path("dist")
        workpath = Path("build")
        distpath.mkdir(parents=True, exist_ok=True)
        workpath.mkdir(parents=True, exist_ok=True)
        out_dir = distpath / name
        cmd = self.build_command()
        print(f"+ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        try:
            self.check_missing_modules(name, workpath)
        except SystemExit:
            # PyInstaller already wrote a broken bundle into out_dir;
            # remove it so a failed build never leaves one behind.
            shutil.rmtree(out_dir, ignore_errors=True)
            raise
        if not out_dir.is_dir():
            raise SystemExit(f"PyInstaller did not produce {out_dir}")
        self.copy_data_files(str(out_dir))
        if args.no_zip:
            print(f"done -> {out_dir}")
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = distpath / f"{name}-{version}-{timestamp}.zip"
        self.bundle_to_zip(str(out_dir), str(zip_path))
        print(f"done -> {zip_path}")
