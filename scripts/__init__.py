"""Build scripts, as a package.

Making scripts/ a package is what lets the per-app build modules
import build_config.py with a normal relative import
(`from ..build_config import DefaultBuildConfig`) instead of
manipulating sys.path. Run them as modules from the repo root —
`python -m scripts.app_one.build` — not as files, and the central
build (scripts/build.py) runs them the same way, with cwd set to
the repo root. scripts/ is outside src/, so it is never shipped
in the distribution.
"""
