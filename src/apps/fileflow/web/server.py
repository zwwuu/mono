# FileFlow — web dashboard server (FastAPI + uvicorn).
#
# Routes (same paths and JSON shapes as the old http.server version,
# so the dashboard JS is unchanged):
#   GET  /                              dashboard page
#   GET  /static/bootstrap.min.css      vendored Bootstrap stylesheet
#   GET  /api/runs                      list runs
#   GET  /api/runs/<id>                 run detail incl. step runs
#   POST /api/runs/<id>/approve        approve at the approval gate
#   POST /api/runs/<id>/reject         reject at the approval gate
#   POST /api/runs/<id>/retry          retry a STUCK run from its step
#   POST /api/runs/<id>/fail           abandon a STUCK run (frees its task)
#   GET  /api/logs?run_id=&after=      poll logs (JSON)
#   GET  /api/logs/stream?run_id=      live logs (Server-Sent Events)
#   GET  /api/tasks                     list tasks
#   POST /api/tasks {...}              create a task
#   GET  /api/tasks/<id>               task detail
#   PUT  /api/tasks/<id>               replace a task
#   DELETE /api/tasks/<id>             delete a task (runs are kept)
#   POST /api/tasks/<id>/run           start a run of a task
#   POST /api/tasks/<id>/enable|disable
#   GET  /api/schedules                list schedules (daily/weekly/monthly)
#   POST /api/schedules {...}          create a schedule (task_id required)
#   POST /api/schedules/<id>/run       run a schedule now (on demand)
#   POST /api/schedules/<id>/enable    enable a schedule
#   POST /api/schedules/<id>/disable   disable a schedule
#   DELETE /api/schedules/<id>         delete a schedule
#
# The dashboard page ships as package data (fileflow/web/static/) so it
# works both from a source checkout and from an installed package.
import json
import os
import time
from datetime import datetime

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from common.logging import log

from ..core.engine import AlreadyRunningError, RunLimitExceeded, count_runs_in_period
from ..core.tasks import TaskError
from ..scheduling import scheduler as sched_mod


def _static_bytes(name):
    try:
        from importlib import resources

        return (resources.files("fileflow.web") / "static" / name).read_bytes()
    except Exception:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name)
        with open(path, "rb") as f:
            return f.read()


def _dashboard_bytes():
    return _static_bytes("dashboard.html")


def _err(message, status):
    return JSONResponse({"error": message}, status_code=status)


def _sse_entries(engine, db, run_id):
    """SSE body generator: backlog first, then live entries, then a
    1s heartbeat. Same shape as the old http.server stream: one
    `data: {...}` JSON log entry per line pair, `: ping` keepalives,
    and dedup by last log id across the backlog/live boundary."""
    last_id = 0
    for entry in db.get_logs(run_id, 0, limit=1000):
        last_id = entry["id"]
        yield f"data: {json.dumps(entry)}\n\n".encode("utf-8")
    q = engine.bus.subscribe(run_id)
    try:
        while True:
            while q:
                entry = q.pop(0)
                if entry["id"] > last_id:
                    last_id = entry["id"]
                    yield f"data: {json.dumps(entry)}\n\n".encode("utf-8")
            # catch anything missed while we were not subscribed
            for entry in db.get_logs(run_id, last_id, limit=200):
                last_id = entry["id"]
                yield f"data: {json.dumps(entry)}\n\n".encode("utf-8")
            yield b": ping\n\n"
            time.sleep(1)
    finally:
        engine.bus.unsubscribe(run_id, q)


def create_app(engine, db, scheduler=None, registry=None):
    app = FastAPI(title="FileFlow")

    def need_registry():
        return registry is not None

    def with_limit_usage(task):
        limit = task.get("run_limit")
        if not limit:
            return task
        period = limit.get("period", "day")
        used = count_runs_in_period(db, task["id"], period)
        task["limit_usage"] = {
            "used": used,
            "max": limit["max"],
            "period": period,
            "remaining": max(0, limit["max"] - used),
        }
        return task

    @app.exception_handler(404)
    async def not_found(_request, _exc):
        return JSONResponse({"error": "not found"}, status_code=404)

    # ------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(content=_dashboard_bytes(), media_type="text/html; charset=utf-8")

    @app.get("/static/bootstrap.min.css")
    def bootstrap_css():
        # Only this vendored file is served: exact path, so no
        # traversal out of web/static/.
        return Response(
            content=_static_bytes("bootstrap.min.css"),
            media_type="text/css; charset=utf-8",
        )

    # ------------------------------------------------------------- runs
    @app.get("/api/runs")
    def list_runs():
        return db.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        try:
            rid = int(run_id)
        except ValueError:
            return _err("bad id", 400)
        run = db.get_run(rid)
        if run is None:
            return _err("not found", 404)
        run["steps"] = db.list_steps(rid)
        return run

    @app.post("/api/runs/{run_id}/{action}")
    def run_action(run_id: str, action: str):
        try:
            rid = int(run_id)
        except ValueError:
            return _err("bad id", 400)
        if action == "approve":
            engine.approve(rid)
        elif action == "reject":
            engine.reject(rid)
        elif action == "retry":
            if not engine.retry(rid):
                return _err("run is not STUCK", 409)
        elif action == "fail":
            if not engine.fail(rid):
                return _err("run is not STUCK", 409)
        else:
            return _err("unknown action", 404)
        return {"ok": True}

    # ------------------------------------------------------------- logs
    @app.get("/api/logs")
    def get_logs(run_id: int = 0, after: int = 0):
        return db.get_logs(run_id, after)

    @app.get("/api/logs/stream")
    def stream_logs(run_id: int = 0):
        # Sync generator: Starlette runs it in a threadpool, so the
        # 1s sleep never blocks the event loop.
        return StreamingResponse(
            _sse_entries(engine, db, run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # ------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    def list_tasks():
        if not need_registry():
            return _err("task registry not available", 503)
        return [with_limit_usage(t) for t in registry.all()]

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        if not need_registry():
            return _err("task registry not available", 503)
        task = registry.get(task_id)
        if task is None:
            return _err("not found", 404)
        return with_limit_usage(task)

    @app.post("/api/tasks", status_code=201)
    def create_task(body: dict = Body(default_factory=dict)):
        if not need_registry():
            return _err("task registry not available", 503)
        try:
            task = registry.create(body)
        except TaskError as e:
            return _err(str(e), 400)
        db.audit("dashboard", "task_create", f"task '{task['id']}'")
        return JSONResponse(task, status_code=201)

    @app.put("/api/tasks/{task_id}")
    def update_task(task_id: str, body: dict = Body(default_factory=dict)):
        if not need_registry():
            return _err("task registry not available", 503)
        if registry.get(task_id) is None:
            return _err("not found", 404)
        try:
            task = registry.update(task_id, body)
        except TaskError as e:
            return _err(str(e), 400)
        db.audit("dashboard", "task_update", f"task '{task['id']}'")
        return task

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str):
        if not need_registry():
            return _err("task registry not available", 503)
        try:
            registry.delete(task_id)
        except TaskError:
            return _err("not found", 404)
        db.audit("dashboard", "task_delete", f"task '{task_id}'")
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/{action}")
    def task_action(task_id: str, action: str, body: dict = Body(default_factory=dict)):
        if not need_registry():
            return _err("task registry not available", 503)
        task = registry.get(task_id)
        if task is None:
            return _err("not found", 404)
        if action == "run":
            try:
                run_id = engine.create_and_start(
                    task["id"],
                    allow_over_limit=bool(body.get("override_limit")),
                )
            except AlreadyRunningError as e:
                return _err(str(e), 409)
            except RunLimitExceeded as e:
                return JSONResponse({"error": str(e), "limit_exceeded": True}, status_code=409)
            except ValueError as e:
                return _err(str(e), 404)
            return {"run_id": run_id}
        if action in ("enable", "disable"):
            updated = registry.set_enabled(task["id"], action == "enable")
            db.audit("dashboard", f"task_{action}", f"task '{task['id']}'")
            return updated
        return _err("unknown action", 404)

    # ------------------------------------------------------------- schedules
    @app.get("/api/schedules")
    def list_schedules():
        out = []
        for s in db.list_schedules():
            s["description"] = sched_mod.describe(s)
            out.append(s)
        return out

    @app.post("/api/schedules", status_code=201)
    def create_schedule(body: dict = Body(default_factory=dict)):
        name = (body.get("name") or "").strip()
        if not name:
            return _err("name is required", 400)
        task_id = body.get("task_id")
        if not task_id:
            return _err("task_id is required", 400)
        if registry is not None and registry.get(task_id) is None:
            return _err(f"unknown task: {task_id!r}", 400)
        try:
            clean = sched_mod.validate_schedule(
                body.get("frequency"),
                body.get("time"),
                body.get("weekday"),
                body.get("day_of_month"),
            )
        except ValueError as e:
            return _err(str(e), 400)
        nxt = sched_mod.compute_next_run(
            clean["frequency"],
            clean["time_of_day"],
            clean["weekday"],
            clean["day_of_month"],
            datetime.now().astimezone(),
        )
        sid = db.create_schedule(
            name,
            clean["frequency"],
            clean["time_of_day"],
            clean["weekday"],
            clean["day_of_month"],
            enabled=body.get("enabled", True),
            next_run_at=nxt.isoformat(timespec="seconds"),
            task_id=task_id,
        )
        db.audit("dashboard", "schedule_create", f"schedule #{sid} '{name}'")
        s = db.get_schedule(sid)
        s["description"] = sched_mod.describe(s)
        return JSONResponse(s, status_code=201)

    @app.post("/api/schedules/{sid}/{action}")
    def schedule_action(sid: str, action: str, body: dict = Body(default_factory=dict)):
        try:
            sid_int = int(sid)
        except ValueError:
            return _err("bad id", 400)
        sched = db.get_schedule(sid_int)
        if sched is None:
            return _err("not found", 404)
        if action == "run":
            if scheduler is None:
                return _err("scheduler not running", 503)
            run_id, note, limit_exceeded = scheduler.run_now(
                sid_int, allow_over_limit=bool(body.get("override_limit"))
            )
            if run_id is None:
                return JSONResponse(
                    {"error": note, "limit_exceeded": limit_exceeded}, status_code=409
                )
            return {"run_id": run_id}
        if action in ("enable", "disable"):
            enabled = action == "enable"
            fields = {"enabled": enabled}
            if enabled:
                nxt = sched_mod.compute_next_run(
                    sched["frequency"],
                    sched["time_of_day"],
                    sched["weekday"],
                    sched["day_of_month"],
                    datetime.now().astimezone(),
                )
                fields["next_run_at"] = nxt.isoformat(timespec="seconds")
            db.update_schedule(sid_int, **fields)
            db.audit("dashboard", f"schedule_{action}", f"schedule #{sid_int}")
            s = db.get_schedule(sid_int)
            s["description"] = sched_mod.describe(s)
            return s
        return _err("unknown action", 404)

    @app.delete("/api/schedules/{sid}")
    def delete_schedule(sid: str):
        try:
            sid_int = int(sid)
        except ValueError:
            return _err("bad id", 400)
        if db.get_schedule(sid_int) is None:
            return _err("not found", 404)
        db.delete_schedule(sid_int)
        db.audit("dashboard", "schedule_delete", f"schedule #{sid_int}")
        return {"ok": True}

    return app


def serve(engine, db, host, port, scheduler=None, registry=None):
    import uvicorn

    log.info(f"FileFlow dashboard: http://{host}:{port}")
    uvicorn.run(
        create_app(engine, db, scheduler, registry),
        host=host,
        port=port,
        access_log=False,
    )
