"""FastAPI dashboard tests: same paths and JSON shapes as the old
http.server version, exercised over a real uvicorn server thread
(stdlib urllib only — no httpx/TestClient needed).

Covers: dashboard page, vendored CSS route, task CRUD, schedule CRUD,
run start/approve/detail, log polling, the SSE log stream, and the
404 contract for unknown paths.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

from helpers import make_engine
from fileflow.infra.demo import ensure_demo_source
from fileflow.web.server import create_app

TASK = {
    "id": "api-task",
    "name": "api-task",
    "enabled": True,
    "approval": True,
    "rename": "{date}_{checksum8}_{name}{ext}",
    "destinations": [
        {"type": "ftp", "dir": "inbox"},
        {"type": "local", "dir": "__data_archive__"},
        {"type": "smb", "dir": ""},
    ],
    "processing": {"dir": "processed", "pattern": "{renamed_name}", "poll_seconds": 1},
    "notify": True,
}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """uvicorn in a daemon thread; `base` once it answers."""

    def __init__(self, app):
        self.app = app
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        import uvicorn

        cfg = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                status, _ = http("GET", f"{self.base}/api/runs")
                if status == 200:
                    return self
            except Exception:
                pass
            time.sleep(0.2)
        raise AssertionError("uvicorn did not come up")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        return False


def http(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_json(method, url, body=None):
    status, raw = http(method, url, body)
    return status, json.loads(raw.decode("utf-8")) if raw else None


def test_dashboard_api():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, registry = make_engine(tmp, [TASK])
        ensure_demo_source(cfg)
        app = create_app(engine, db, scheduler=None, registry=registry)
        with Server(app) as srv:
            # pages
            status, body = http("GET", f"{srv.base}/")
            assert status == 200 and b"bootstrap.min.css" in body
            status, body = http("GET", f"{srv.base}/static/bootstrap.min.css")
            assert status == 200 and body.startswith(b"@charset")
            status, body = http_json("GET", f"{srv.base}/static/other.css")
            assert status == 404 and body == {"error": "not found"}
            status, body = http_json("GET", f"{srv.base}/api/nope")
            assert status == 404 and body == {"error": "not found"}

            # tasks: list, create (bad + good), update, delete
            status, tasks = http_json("GET", f"{srv.base}/api/tasks")
            assert status == 200 and [t["id"] for t in tasks] == ["api-task"]
            status, body = http_json("POST", f"{srv.base}/api/tasks", {"id": "BAD ID"})
            assert status == 400 and "error" in body
            status, task = http_json(
                "POST",
                f"{srv.base}/api/tasks",
                {"id": "api-two", "name": "api-two", "enabled": True, "approval": True},
            )
            assert status == 201 and task["id"] == "api-two"
            status, task = http_json(
                "PUT", f"{srv.base}/api/tasks/api-two", {"name": "api-two-renamed"}
            )
            assert status == 200 and task["name"] == "api-two-renamed"
            status, body = http_json("DELETE", f"{srv.base}/api/tasks/api-two")
            assert status == 200 and body == {"ok": True}
            status, _ = http_json("GET", f"{srv.base}/api/tasks/api-two")
            assert status == 404

            # schedules: validation, create, delete
            status, body = http_json("POST", f"{srv.base}/api/schedules", {"name": "no task"})
            assert status == 400 and body["error"] == "task_id is required"
            status, sched = http_json(
                "POST",
                f"{srv.base}/api/schedules",
                {
                    "name": "daily api",
                    "task_id": "api-task",
                    "frequency": "daily",
                    "time": "07:30",
                },
            )
            assert status == 201 and sched["description"]
            status, body = http_json("DELETE", f"{srv.base}/api/schedules/{sched['id']}")
            assert status == 200 and body == {"ok": True}

            # run: start via API, poll logs, approve via API, detail
            status, body = http_json("POST", f"{srv.base}/api/tasks/api-task/run", {})
            assert status == 200 and body["run_id"]
            run_id = body["run_id"]
            deadline = time.time() + 15
            logs = []
            while time.time() < deadline:
                status, logs = http_json("GET", f"{srv.base}/api/logs?run_id={run_id}&after=0")
                assert status == 200
                if logs:
                    break
                time.sleep(0.2)
            assert logs, "run produced no logs"
            status, body = http_json("POST", f"{srv.base}/api/runs/{run_id}/approve", {})
            assert status == 200 and body == {"ok": True}
            status, run = http_json("GET", f"{srv.base}/api/runs/{run_id}")
            assert status == 200 and "steps" in run

            # SSE stream: backlog lines arrive immediately, as JSON
            req = urllib.request.Request(f"{srv.base}/api/logs/stream?run_id={run_id}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 200
                assert "text/event-stream" in resp.headers.get_content_type()
                seen = []
                deadline = time.time() + 10
                while time.time() < deadline and not seen:
                    line = resp.readline().decode("utf-8").strip()
                    if line.startswith("data:"):
                        seen.append(json.loads(line[5:]))
                assert seen, "SSE stream delivered no data lines"
                assert seen[0]["run_id"] == run_id
