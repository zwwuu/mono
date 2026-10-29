"""Tests for the default build config: zip bundling, data copying,
and command construction.

APP_NAME and VERSION are set on the build class by the per-app
script (imported from the app's meta.py), so these tests set them
directly — there is no meta.py loading left to test. Tests use
synthetic fixture apps in tmp dirs — never the shipped sample apps
or shared libraries — so they stay valid whatever apps the
template holds. The build config under test is loaded as a
module, the same way the per-app build scripts load it.
"""

import importlib.util
import shutil
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_config", ROOT / "scripts" / "build_config.py")
build_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_config)


class FixtureBuild(build_config.DefaultBuildConfig):
    APP_NAME = "fake-app"
    VERSION = "1.2.3"
    PACKAGE = "fake_app"


def make_fixture(tmp_path, version="1.2.3"):
    """A minimal fake app repo: root/src/fake_app/ with data folders.

    The fixture's meta.py is inert here: the build class carries
    APP_NAME/VERSION directly, as a real per-app script would after
    importing them from its app's meta.py.
    """
    root = tmp_path / "repo"
    pkg = root / "src" / "fake_app"
    (pkg / "config").mkdir(parents=True, exist_ok=True)
    (pkg / "assets").mkdir(parents=True, exist_ok=True)
    (pkg / "meta.py").write_text(
        f'APP_NAME = "fake-app"\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (pkg / "__init__.py").write_text('"""Fixture package init."""\n', encoding="utf-8")
    (pkg / "__main__.py").write_text("", encoding="utf-8")
    (pkg / "config" / "config.json").write_text('{"x": 1}', encoding="utf-8")
    (pkg / "assets" / "sample.txt").write_text("sample", encoding="utf-8")
    return root, pkg


def fixture_builder(tmp_path, cls=FixtureBuild, **overrides):
    """A build of the given class pointed at a fresh fixture repo."""
    root, pkg = make_fixture(tmp_path)
    builder = cls()
    builder.root = str(root)
    builder.src_dir = str(root / "src")
    builder.package_dir = str(pkg)
    for key, value in overrides.items():
        setattr(builder, key, value)
    return builder


def test_build_command_uses_explicit_app_name(tmp_path):
    builder = fixture_builder(tmp_path)
    cmd = builder.build_command(builder.parse_args([]))
    assert cmd[cmd.index("--name") + 1] == "fake-app"


def test_build_command_missing_app_name_raises(tmp_path):
    builder = fixture_builder(tmp_path, APP_NAME=None)
    with pytest.raises(SystemExit, match="APP_NAME not set"):
        builder.build_command(builder.parse_args([]))


def test_main_missing_version_raises(tmp_path):
    builder = fixture_builder(tmp_path, VERSION=None)
    with pytest.raises(SystemExit, match="VERSION not set"):
        builder.main([])


def test_zip_roots_entries_at_folder_name(tmp_path):
    out = tmp_path / "fake-app"
    (out / "_internal").mkdir(parents=True)
    (out / "_internal" / "x.txt").write_text("x", encoding="utf-8")
    (out / "fake-app").write_text("exe", encoding="utf-8")
    zip_path = tmp_path / "fake-app-1.2.3-20260101-000000.zip"
    FixtureBuild().bundle_to_zip(str(out), str(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == ["fake-app/_internal/x.txt", "fake-app/fake-app"]


def test_copy_data_files_places_flat(tmp_path):
    builder = fixture_builder(tmp_path, EXTRA_DATAS=(("src/fake_app/extra.txt", "extra.txt"),))
    (Path(builder.root) / "src" / "fake_app" / "extra.txt").write_text("extra", encoding="utf-8")
    out = tmp_path / "fake-app"
    (out / "config").mkdir(parents=True)
    (out / "config" / "stale.txt").write_text("stale", encoding="utf-8")
    builder.copy_data_files(str(out))
    assert (out / "config" / "config.json").is_file()
    assert (out / "assets" / "sample.txt").is_file()
    assert (out / "extra.txt").is_file()
    assert not (out / "config" / "stale.txt").exists()


def test_copy_data_files_replaces_stale_file_with_dir(tmp_path):
    builder = fixture_builder(tmp_path)
    out = tmp_path / "fake-app"
    out.mkdir()
    (out / "config").write_text("stale file", encoding="utf-8")
    builder.copy_data_files(str(out))
    assert (out / "config").is_dir()
    assert (out / "config" / "config.json").is_file()


def test_copy_data_files_missing_source_fails(tmp_path):
    builder = fixture_builder(tmp_path, EXTRA_DATAS=(("src/no-such-file.txt", "no-such-file.txt"),))
    with pytest.raises(SystemExit):
        builder.copy_data_files(str(tmp_path / "fake-app"))


def test_copy_data_files_skips_missing_folder(tmp_path):
    builder = fixture_builder(tmp_path)
    # An app without an assets/ folder: copying must not fail, and
    # must not create an empty assets/ in the output.
    shutil.rmtree(Path(builder.package_dir) / "assets")
    out = tmp_path / "fake-app"
    builder.copy_data_files(str(out))
    assert (out / "config" / "config.json").is_file()
    assert not (out / "assets").exists()


def test_build_command_uses_onedir_and_no_add_data(tmp_path):
    builder = fixture_builder(tmp_path)
    cmd = builder.build_command(builder.parse_args([]))
    assert "--add-data" not in cmd
    assert "--onedir" in cmd
    assert cmd[cmd.index("--paths") + 1] == str(Path(builder.root) / "src")
    assert cmd[-1] == str(Path(builder.package_dir) / "__main__.py")


def test_subclass_override_changes_command(tmp_path):
    builder = fixture_builder(tmp_path, CONSOLE=False)
    cmd = builder.build_command(builder.parse_args([]))
    assert "--windowed" in cmd
    assert "--console" not in cmd


def test_copy_data_files_creates_parent_dirs_for_file_dest(tmp_path):
    builder = fixture_builder(
        tmp_path, EXTRA_DATAS=(("src/fake_app/extra.txt", "sub/dir/extra.txt"),)
    )
    (Path(builder.root) / "src" / "fake_app" / "extra.txt").write_text("extra", encoding="utf-8")
    out = tmp_path / "fake-app"
    builder.copy_data_files(str(out))
    assert (out / "sub" / "dir" / "extra.txt").read_text(encoding="utf-8") == "extra"
