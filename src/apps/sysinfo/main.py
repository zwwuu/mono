"""sysinfo CLI: print runtime system, user, and network info as JSON."""

import json

from common.app_config import AppConfig
from common.sysinfo import all_info

from . import meta


class SysinfoApp(AppConfig):
    APP_NAME = meta.APP_NAME
    VERSION = meta.__version__
    DESCRIPTION = "Print runtime system, user, and network info as JSON."

    def run(self):
        self.LOGGER.info("printing system info")
        print(json.dumps(all_info(), indent=2))
        return 0


def main(argv=None):
    return SysinfoApp.main(anchor=__file__, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
