"""sysinfo CLI: print runtime system, user, and network info as JSON."""

import json

from common.app_config import AppConfig
from common.sysinfo import all_info

from . import meta


class SysinfoConfig(AppConfig):
    APP_NAME = meta.APP_NAME
    VERSION = meta.__version__
    DESCRIPTION = "Print runtime system, user, and network info as JSON."


def main(argv=None):
    cfg = SysinfoConfig(anchor=__file__, argv=argv)
    cfg.LOGGER.info("printing system info")
    print(json.dumps(all_info(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
