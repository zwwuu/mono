# FileFlow — application wiring: build Config, DB, TaskRegistry, Engine
# and Scheduler from a config file, and serve the dashboard. Used by the
# CLI and by anything embedding FileFlow as a library.
from . import __version__
from .config import load_config
from .core.engine import Engine
from .core.tasks import TaskRegistry
from .infra.db import DB
from .infra.logging_setup import setup_logging
from .scheduling.scheduler import Scheduler


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        # Logging first: console + date-rotated file under the data dir.
        setup_logging(
            cfg.data_dir,
            level=cfg.get("app", "log_level", "INFO"),
            retention_days=int(cfg.get("app", "log_retention_days", 30)),
        )
        from loguru import logger

        logger.info(f"FileFlow {__version__} starting (data_dir={cfg.data_dir})")
        self.db = DB(cfg.path("fileflow.db"))
        self.registry = TaskRegistry(cfg)
        self.engine = Engine(cfg, self.db, self.registry)
        self.scheduler = Scheduler(cfg, self.db, self.engine, self.registry)
        self._demo_processor = None

    @classmethod
    def from_config(cls, path=None):
        return cls(load_config(path))

    def start(self):
        """Start background workers: the scheduler and, in demo mode,
        the simulated external processor."""
        self.scheduler.start()
        if self.cfg.get("demo", "enabled", False):
            from .infra.demo import DemoProcessor, ensure_demo_source

            ensure_demo_source(self.cfg)
            self._demo_processor = DemoProcessor(
                self.cfg, lambda wid, msg, level="INFO": None, self.registry
            )
            self._demo_processor.start()

    def stop(self):
        self.scheduler.stop()

    def serve(self, host, port):
        from .web.server import serve

        serve(self.engine, self.db, host, port, self.scheduler, self.registry)
