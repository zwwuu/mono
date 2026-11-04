"""
routers/scheduled_tasks.py
--------------------------
Admin UI and API routes for managing scheduled reminder/escalation tasks.

Routes
------
GET  /admin/scheduled-tasks         — List all tasks with filter options
POST /admin/scheduled-tasks/{id}/cancel  — Cancel a pending task
GET  /api/scheduled-tasks           — JSON list (for programmatic access)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.config import settings
from app.database import get_db
from app.dependencies import require_roles
from app.models.workflow import ScheduledTask
from app.models.user import User
from app.services.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin/scheduled-tasks", name="admin_scheduled_tasks")
def list_scheduled_tasks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "approver"])),
    status_filter: Optional[str] = None,
    workflow_code: Optional[str] = None,
):
    """Admin page listing all scheduled reminder/escalation tasks."""
    query = db.query(ScheduledTask).order_by(desc(ScheduledTask.created_at))

    if status_filter == "pending":
        query = query.filter(ScheduledTask.is_fired == False, ScheduledTask.is_cancelled == False)  # noqa: E712
    elif status_filter == "fired":
        query = query.filter(ScheduledTask.is_fired == True)  # noqa: E712
    elif status_filter == "cancelled":
        query = query.filter(ScheduledTask.is_cancelled == True)  # noqa: E712

    if workflow_code:
        query = query.filter(ScheduledTask.workflow_tracking_code.ilike(f"%{workflow_code}%"))

    tasks = query.limit(200).all()

    # Read and clear flash message from session
    flash_msg = request.session.pop("flash_success", None)
    flash_err = request.session.pop("flash_error", None)
    message = flash_msg or flash_err
    message_type = "success" if flash_msg else ("danger" if flash_err else "info")

    return templates.TemplateResponse(
        request=request,
        name="admin/scheduled_tasks.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "active_nav": "scheduled_tasks",
            "tasks": tasks,
            "status_filter": status_filter or "all",
            "workflow_code": workflow_code or "",
            "scheduler_running": SchedulerService.is_running(),
            "pending_count": db.query(ScheduledTask).filter(
                ScheduledTask.is_fired == False, ScheduledTask.is_cancelled == False  # noqa: E712
            ).count(),
            "message": message,
            "message_type": message_type,
        },
    )


@router.post("/admin/scheduled-tasks/{job_id}/cancel", name="cancel_scheduled_task")
def cancel_task(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "approver"])),
):
    """Cancel a pending scheduled task."""
    cancelled = SchedulerService.cancel_task(db, job_id)
    if not cancelled:
        request.session["flash_error"] = "Task could not be cancelled (already fired or not found)."
    else:
        request.session["flash_success"] = f"Scheduled task cancelled."
    return RedirectResponse(url="/admin/scheduled-tasks", status_code=303)


@router.get("/api/scheduled-tasks", name="api_scheduled_tasks")
def api_list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "approver"])),
):
    """JSON API for listing scheduled tasks."""
    tasks = db.query(ScheduledTask).order_by(desc(ScheduledTask.created_at)).limit(500).all()
    return [
        {
            "id": t.id,
            "job_id": t.job_id,
            "workflow_tracking_code": t.workflow_tracking_code,
            "task_type": t.task_type,
            "description": t.description,
            "run_at": t.run_at.isoformat() if t.run_at else None,
            "recipient_role": t.recipient_role,
            "is_fired": t.is_fired,
            "is_cancelled": t.is_cancelled,
            "fired_at": t.fired_at.isoformat() if t.fired_at else None,
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]
