# FileFlow — demo helpers: sample source file + simulated external processor.
#
# In demo mode the "FTP server" is a local folder tree under demo_ftp/.
# This processor watches each inbox directory and, after a short delay,
# writes a transformed copy into the matching processed directory —
# standing in for the external system that processes files dropped on
# the real FTP server.
#
# The inbox → processed mapping comes from the tasks themselves: the
# default "inbox" → "processed" pair always applies, plus one pair per
# task (its first FTP copy dir → its first wait_processed dir, the same
# convention the shorthand pipeline builder uses). A task with a custom
# FTP inbox therefore gets demo processing without any extra setup.
import os
import threading
import time

SAMPLE = """id,customer,amount,status
1,Acme Corp,1200.50,pending
2,Globex,349.99,pending
3,Initech,8999.00,pending
"""


def ensure_demo_source(cfg):
    path = cfg.path("demo_source", cfg.get("demo", "source_file", "input_report.csv"))
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)
    return path


def demo_mappings(tasks):
    """Inbox → processed dir mapping for the demo processor."""
    from common.pipeline.builder import build_pipeline

    mapping = {"inbox": "processed"}
    for task in tasks or []:
        try:
            pipeline = build_pipeline(task)
        except Exception:
            continue
        ftp_dirs = [
            step[3]["to"]["dir"]
            for step in pipeline
            if step[2] == "copy" and step[3].get("to", {}).get("type") == "ftp"
        ]
        wait_dirs = [step[3].get("dir") for step in pipeline if step[2] == "wait_processed"]
        if ftp_dirs and wait_dirs:
            mapping.setdefault(ftp_dirs[0], wait_dirs[0])
    return mapping


class DemoProcessor(threading.Thread):
    def __init__(self, cfg, engine_log, registry=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log = engine_log
        self.registry = registry
        self.delay = int(cfg.get("demo", "process_delay_seconds", 8))
        self._seen = set()

    def run(self):
        while True:
            try:
                tasks = self.registry.all() if self.registry else []
                for inbox, processed in demo_mappings(tasks).items():
                    inbox_dir = self.cfg.path("demo_ftp", inbox)
                    processed_dir = self.cfg.path("demo_ftp", processed)
                    os.makedirs(inbox_dir, exist_ok=True)
                    os.makedirs(processed_dir, exist_ok=True)
                    for name in os.listdir(inbox_dir):
                        src = os.path.join(inbox_dir, name)
                        dest = os.path.join(processed_dir, name)
                        key = (inbox, name)
                        if key in self._seen or os.path.exists(dest) or not os.path.isfile(src):
                            continue
                        self._seen.add(key)
                        time.sleep(self.delay)
                        with open(src, encoding="utf-8") as f:
                            content = f.read()
                        with open(dest, "w", encoding="utf-8") as f:
                            f.write(content.replace("pending", "PROCESSED"))
                        self.log(0, f"[demo processor] processed {name}: {inbox}/ -> {processed}/")
            except Exception:
                pass
            time.sleep(2)
