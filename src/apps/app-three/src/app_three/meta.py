"""app_three metadata — constants only.

The build reads APP_NAME and __version__ from here, and
__init__.py re-exports them, so the name and version are
defined once. Keep this file free of imports: the build
loads it directly.
"""

APP_NAME = "app-three"
__version__ = "0.1.0"
