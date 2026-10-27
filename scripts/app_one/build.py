import os
from ...build_config import  DefaultBuildConfig
from apps.app_one.meta import APP_NAME, __version__


SCRIPTS_DIR = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class AppOneBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    ENTRY_POINT = os.path.join(SCRIPTS_DIR, APP_NAME, 'main.py')
    CONSOLE = True

if __name__ == "__main__":
    AppOneBuild().main()
