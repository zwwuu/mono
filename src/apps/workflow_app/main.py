from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_db
from app.routers import auth_router, workflows_router, dev_router, api_router, scheduled_tasks_router
from app.services.scheduler_service import SchedulerService


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
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Register routers
app.include_router(auth_router)
app.include_router(workflows_router)
app.include_router(dev_router)
app.include_router(api_router)
app.include_router(scheduled_tasks_router)


from fastapi.responses import JSONResponse
from app.dependencies import RedirectException


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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    start()
