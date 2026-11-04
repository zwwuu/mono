from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from apps.workflow_app.config import settings
from apps.workflow_app.database import init_db
from apps.workflow_app.paths import app_dir
from apps.workflow_app.routers import (
    api_router,
    auth_router,
    dev_router,
    scheduled_tasks_router,
    workflows_router,
)
from apps.workflow_app.services.scheduler_service import SchedulerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure database tables & demo users are initialized
    init_db()
    SchedulerService.start()
    yield
    # Shutdown: stop background scheduler gracefully
    SchedulerService.stop()



app = FastAPI(
    title=settings.APP_NAME,
    description="Human-in-the-loop interactive workflow system with LDAP, multi-step approvals, and mailing notifications.",
    version="1.0.0",
    lifespan=lifespan
)

# Cookie session middleware for clean browser forms and navigation
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie=settings.SESSION_COOKIE_NAME,
    max_age=settings.SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=not settings.DEBUG
)

# Mount static assets
app.mount("/static", StaticFiles(directory=str(app_dir() / "static")), name="static")

# Register routers
app.include_router(auth_router)
app.include_router(workflows_router)
app.include_router(dev_router)
app.include_router(api_router)
app.include_router(scheduled_tasks_router)


from fastapi.responses import JSONResponse

from apps.workflow_app.dependencies import RedirectException


@app.exception_handler(RedirectException)
async def redirect_exception_handler(request: Request, exc: RedirectException):
    return RedirectResponse(url=exc.url, status_code=302)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


def start():
    """CLI entrypoint to run the uvicorn development server."""
    import uvicorn
    uvicorn.run("apps.workflow_app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    start()
