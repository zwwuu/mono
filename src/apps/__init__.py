"""Apps in the monorepo, one package per app under src/apps/.

Import an app as apps.<name>, e.g. ``from apps.app_one import main``.
Adding apps/__init__.py (instead of relying on a namespace package)
means setuptools finds these packages with a plain packages.find
in pyproject.toml.
"""
