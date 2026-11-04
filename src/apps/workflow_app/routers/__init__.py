from apps.workflow_app.routers.api import router as api_router
from apps.workflow_app.routers.auth import router as auth_router
from apps.workflow_app.routers.dev_tools import router as dev_router
from apps.workflow_app.routers.scheduled_tasks import router as scheduled_tasks_router
from apps.workflow_app.routers.workflows import router as workflows_router

__all__ = ["auth_router", "workflows_router", "dev_router", "api_router", "scheduled_tasks_router"]
