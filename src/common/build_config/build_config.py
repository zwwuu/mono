import argparse
import datetime
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


class DefaultBuildConfig:
    """Build one app with PyInstaller and bundle the output into a zip."""
    APP_NAME = None
    VERSION = None
    ENTRY_POINT = None
    CONSOLE = True
    EXTRA_DATAS = ()

    def __init__(self):
        self.root = str(Path(__file__).resolve().parents[1])
        self.src_dir = str(Path(self.root) / "src")

    # -- PyInstaller -----------------------------------------------------

    def entry_point(self):
        """The file PyInstaller builds: the package's __main__.py."""
        entry = Path(self.ENTRY_POINT) / "__main__.py"
        if not entry.is_file():
            raise SystemExit(f"entry point not found: {entry}")
        return str(entry)

    def build_command(self, args=None):
        """The full PyInstaller command line for this app."""
        if args is None:
            args = self.parse_args([])
        name = args.name or self.APP_NAME
        if not name:
            raise SystemExit("APP_NAME not set in build script (import it from the app's meta.py)")
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--name",
            name,
        ]
        cmd.append("--console" if self.CONSOLE else "--windowed")
        cmd.append("--onedir")
        cmd.append(self.entry_point())
        return cmd

    # -- data files and zip ----------------------------------------------

    def copy_data_files(self, out_dir):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # The app's data folders, copied as-is. Not a knob: every app
        for src_rel, dest_rel in self.EXTRA_DATAS:
            src_path = Path(self.root) / src_rel
            if not src_path.exists():
                raise SystemExit(f"extra data not found: {src_path}")
            dest_path = out / dest_rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
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
        version = self.VERSION
        if not version:
            raise SystemExit("VERSION not set in build script (import it from the app's meta.py)")
        distpath =  Path(self.root) / "dist"
        distpath.mkdir(parents=True, exist_ok=True)
        out_dir = distpath / self.APP_NAME
        cmd = self.build_command()
        print(f"+ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        if not out_dir.is_dir():
            raise SystemExit(f"PyInstaller did not produce {out_dir}")
        self.copy_data_files(str(out_dir))
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = distpath / f"{self.APP_NAME}-{version}-{timestamp}.zip"
        self.bundle_to_zip(str(out_dir), str(zip_path))
        print(f"done -> {zip_path}")
