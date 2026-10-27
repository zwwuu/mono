"""Tests for the default build config: version and app-name reading,
zip bundling, data copying, and command construction.

Tests use synthetic fixture apps in tmp dirs — never the shipped
sample apps or shared libraries — so they stay valid whatever apps
the template holds. The build config under test is loaded as a
module, the same way the per-app build scripts load it.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_config", ROOT / "scripts" / "build_config.py")
build_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_config)


class FixtureBuild(build_config.DefaultBuildConfig):
    APP_NAME = "fake-app"
    PACKAGE = "fake_app"


class FallbackBuild(build_config.DefaultBuildConfig):
    """A build that takes its app name from the package's meta.py."""

    PACKAGE = "fake_app"


class ExplicitBuild(build_config.DefaultBuildConfig):
    """An explicit APP_NAME wins over the package's meta.py."""

    APP_NAME = "other-app"
    PACKAGE = "fake_app"


def make_fixture(tmp_path, version="1.2.3"):
    """A minimal fake app repo: root/apps/fake-app/src/fake_app/ with data folders.

    meta.py holds the constants; __init__.py is a stub — the build
    reads meta.py, never __init__.py.
    """
    root = tmp_path / "repo"
    pkg = root / "apps" / "fake-app" / "src" / "fake_app"
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
    builder.app_dir = str(pkg.parents[1])
    builder.src_dir = str(pkg.parent)
    builder.package_dir = str(pkg)
    for key, value in overrides.items():
        setattr(builder, key, value)
    return builder


def test_read_version_matches_package(tmp_path):
    assert fixture_builder(tmp_path).read_version() == "1.2.3"


def test_read_version_handles_annotated_assignment(tmp_path):
    builder = fixture_builder(tmp_path)
    (Path(builder.package_dir) / "meta.py").write_text(
        '__version__: str = "9.9.9"\n', encoding="utf-8"
    )
    assert builder.read_version() == "9.9.9"


def test_read_version_missing_raises(tmp_path):
    builder = fixture_builder(tmp_path)
    (Path(builder.package_dir) / "meta.py").write_text("__version__: str\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        builder.read_version()


def make_fallback_fixture(tmp_path, meta_text):
    """A fake app package at tmp_path/apps/fake-app/src/fake_app/ for fallback tests.

    FallbackBuild resolves its dirs from build_config.py's location, so
    these tests point the module at tmp_path to make it read here.
    meta.py holds the given text; __init__.py is a stub — the build
    reads meta.py, never __init__.py.
    """
    pkg = tmp_path / "apps" / "fake-app" / "src" / "fake_app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "meta.py").write_text(meta_text, encoding="utf-8")
    (pkg / "__init__.py").write_text('"""Fixture package init."""\n', encoding="utf-8")
    return pkg


def test_app_name_defaults_to_package_meta(tmp_path, monkeypatch):
    make_fallback_fixture(tmp_path, 'APP_NAME = "fake-app"\n__version__ = "1.2.3"\n')
    monkeypatch.setattr(build_config, "__file__", str(tmp_path / "scripts" / "build_config.py"))
    assert FallbackBuild().APP_NAME == "fake-app"


def test_app_name_handles_annotated_assignment(tmp_path, monkeypatch):
    make_fallback_fixture(tmp_path, 'APP_NAME: str = "renamed-app"\n__version__ = "1.2.3"\n')
    monkeypatch.setattr(build_config, "__file__", str(tmp_path / "scripts" / "build_config.py"))
    assert FallbackBuild().APP_NAME == "renamed-app"


def test_app_name_computed_value_resolves(tmp_path, monkeypatch):
    # importlib executes the module, so unlike a static parse this
    # also resolves values that are computed, not literal.
    make_fallback_fixture(tmp_path, 'APP_NAME = "fake" + "-app"\n__version__ = "1.2.3"\n')
    monkeypatch.setattr(build_config, "__file__", str(tmp_path / "scripts" / "build_config.py"))
    assert FallbackBuild().APP_NAME == "fake-app"


def test_app_name_missing_raises(tmp_path, monkeypatch):
    make_fallback_fixture(tmp_path, '__version__ = "1.2.3"\n')
    monkeypatch.setattr(build_config, "__file__", str(tmp_path / "scripts" / "build_config.py"))
    with pytest.raises(SystemExit, match="APP_NAME not found"):
        FallbackBuild()


def test_app_name_missing_meta_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(build_config, "__file__", str(tmp_path / "scripts" / "build_config.py"))
    with pytest.raises(SystemExit, match="meta.py not found"):
        FallbackBuild()


def test_explicit_app_name_wins_over_package_init(tmp_path):
    builder = fixture_builder(tmp_path, cls=ExplicitBuild)
    assert builder.APP_NAME == "other-app"


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
    (Path(builder.package_dir) / "extra.txt").write_text("extra", encoding="utf-8")
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


def test_copy_data_files_respects_data_folders_override(tmp_path):
    builder = fixture_builder(tmp_path, DATA_FOLDERS=("config",))
    out = tmp_path / "fake-app"
    builder.copy_data_files(str(out))
    assert (out / "config" / "config.json").is_file()
    assert not (out / "assets").exists()

    builder = fixture_builder(tmp_path, DATA_FOLDERS=("no-such-folder",))
    # A missing folder is skipped silently, not an error.
    builder.copy_data_files(str(tmp_path / "empty"))


def test_build_command_uses_onedir_and_no_add_data(tmp_path):
    builder = fixture_builder(tmp_path)
    cmd = builder.build_command(builder.parse_args([]))
    assert "--add-data" not in cmd
    assert "--onedir" in cmd
    assert cmd[cmd.index("--paths") + 1] == str(Path(builder.app_dir) / "src")
    assert cmd[-1] == str(Path(builder.package_dir) / "__main__.py")


def test_subclass_override_changes_command(tmp_path):
    builder = fixture_builder(tmp_path, CONSOLE=False, HIDDEN_IMPORTS=("some_plugin",))
    cmd = builder.build_command(builder.parse_args([]))
    assert "--windowed" in cmd
    assert "--hidden-import" in cmd
    assert "some_plugin" in cmd


def test_copy_data_files_creates_parent_dirs_for_file_dest(tmp_path):
    builder = fixture_builder(
        tmp_path, EXTRA_DATAS=(("src/fake_app/extra.txt", "sub/dir/extra.txt"),)
    )
    (Path(builder.package_dir) / "extra.txt").write_text("extra", encoding="utf-8")
    out = tmp_path / "fake-app"
    builder.copy_data_files(str(out))
    assert (out / "sub" / "dir" / "extra.txt").read_text(encoding="utf-8") == "extra"


def test_meta_module_is_loaded_once(tmp_path):
    builder = fixture_builder(tmp_path)
    first, _ = builder._load_meta_module()
    second, _ = builder._load_meta_module()
    assert first is second
