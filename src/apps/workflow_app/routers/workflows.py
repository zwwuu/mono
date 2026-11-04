from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.user import User
from app.models.workflow import WorkflowInstance, WorkflowStepHistory, AuditLog
from app.dependencies import require_user
from app.services.workflow_engine import WorkflowEngine, WORKFLOW_REGISTRY
from app.services.mail_service import EmailService
from app.services.scheduler_service import SchedulerService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index_redirect():
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    filter: Optional[str] = None,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    query = db.query(WorkflowInstance).order_by(WorkflowInstance.updated_at.desc())
    
    if filter == "my_requests":
        query = query.filter(WorkflowInstance.requester_id == current_user.id)
    elif filter == "my_action":
        if current_user.role != "admin":
            query = query.filter(
                WorkflowInstance.assigned_role == current_user.role,
                WorkflowInstance.status.notin_(["APPROVED", "REJECTED", "CONFIRMED"])
            )

    workflows = query.all()

    # Calculate metrics
    all_workflows = db.query(WorkflowInstance).all()
    pending_my_action = sum(1 for w in all_workflows if WorkflowEngine.can_user_act(w, current_user))
    in_progress = sum(1 for w in all_workflows if w.status in ["DRAFT", "PENDING_REVIEW", "IN_REVIEW", "PENDING_APPROVAL", "REVISION_REQUESTED"])
    approved = sum(1 for w in all_workflows if w.status in ["APPROVED", "CONFIRMED"])
    total = len(all_workflows)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "active_nav": "dashboard",
            "workflows": workflows,
            "metrics": {
                "pending_my_action": pending_my_action,
                "in_progress": in_progress,
                "approved": approved,
                "total": total
            },
            "can_user_act_func": WorkflowEngine.can_user_act
        }
    )


from app.services.workflow_engine import WorkflowEngine, WorkflowRegistry


@router.get("/workflows/new", response_class=HTMLResponse)
async def new_workflow_form(
    request: Request,
    type: Optional[str] = "purchase_request",
    current_user: User = Depends(require_user)
):
    available_types = WorkflowRegistry.list_all()
    selected_def = WorkflowRegistry.get(type) or available_types[0]
    initial_step_def = selected_def.get_step(selected_def.initial_step)

    return templates.TemplateResponse(
        request=request,
        name="workflows/create.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "active_nav": "new",
            "available_types": available_types,
            "selected_type": selected_def.type_id,
            "workflow_def": selected_def,
            "step_def": initial_step_def
        }
    )


@router.post("/workflows/new")
async def create_workflow(
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    form_data = await request.form()
    workflow_type = form_data.get("workflow_type", "purchase_request")
    action = form_data.get("action", "SUBMIT")

    definition = WorkflowRegistry.get(workflow_type)
    if not definition:
        raise HTTPException(status_code=400, detail=f"Unknown workflow type '{workflow_type}'")

    initial_step_def = definition.get_step(definition.initial_step)
    if not initial_step_def:
        raise HTTPException(status_code=400, detail="Invalid initial workflow step definition")

    # Generate sequential unique tracking code with type prefix
    count = db.query(WorkflowInstance).count() + 1
    year = datetime.now(timezone.utc).year
    prefix = definition.type_id[:3].upper()
    tracking_code = f"{prefix}-{year}-{count:04d}"

    # Extract title or infer from first field
    title = form_data.get("title")
    if not title:
        first_field_name = initial_step_def.fields[0].name if initial_step_def.fields else None
        first_val = form_data.get(first_field_name) if first_field_name else None
        title = f"{definition.title}: {first_val}" if first_val else f"{definition.title} #{count}"

    # Collect step 1 dynamic payload
    step_1_payload = {}
    for key, val in form_data.items():
        if key not in ["workflow_type", "title", "action"]:
            step_1_payload[key] = val

    # Determine transition
    if action not in initial_step_def.transitions:
        action = "SUBMIT" if "SUBMIT" in initial_step_def.transitions else list(initial_step_def.transitions.keys())[0]

    next_step_id, next_status, next_role = initial_step_def.transitions[action]

    workflow = WorkflowInstance(
        tracking_code=tracking_code,
        title=title,
        workflow_type=workflow_type,
        current_step=next_step_id,
        status=next_status,
        requester_id=current_user.id,
        assigned_role=next_role,
        form_data=step_1_payload
    )
    db.add(workflow)
    db.flush()

    # Record history
    history = WorkflowStepHistory(
        workflow_id=workflow.id,
        step_name=initial_step_def.title,
        action=action,
        actor_id=current_user.id,
        actor_name=current_user.display_name,
        comment="Submitted initial request" if action == "SUBMIT" else "Saved initial draft",
        step_snapshot=step_1_payload
    )
    db.add(history)

    # Audit log
    audit = AuditLog(
        action=f"WORKFLOW_{action}",
        entity_type="workflow",
        entity_id=tracking_code,
        actor_username=current_user.username,
        details=f"Created {definition.title} '{title}' with status {next_status}"
    )
    db.add(audit)
    db.commit()

    # Schedule reminders for initial step
    SchedulerService.schedule_step_reminders(db, workflow, next_step_id)
    db.commit()

    # Trigger notifications for assigned role if submitted
    if next_role and next_role != "requester":
        all_active_users = db.query(User).filter(User.is_active == True).all()
        notified_emails = set()
        for rev in all_active_users:
            if rev.has_role(next_role, workflow.workflow_type) and rev.email and rev.email not in notified_emails:
                notified_emails.add(rev.email)
                EmailService.notify_step_assigned(
                    workflow=workflow,
                    target_email=rev.email,
                    role_name=rev.display_name,
                    step_name=next_step_id
                )

    return RedirectResponse(url=f"/workflows/{workflow.id}", status_code=status.HTTP_302_FOUND)


@router.get("/workflows/{workflow_id}", response_class=HTMLResponse)
async def view_workflow(
    workflow_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    definition = WorkflowEngine.get_definition(workflow.workflow_type)
    can_act = WorkflowEngine.can_user_act(workflow, current_user)
    stages = definition.get_progress_stages(workflow.current_step, workflow.status) if definition else []
    approval_progress = WorkflowEngine.get_approval_progress(workflow, current_user)

    return templates.TemplateResponse(
        request=request,
        name="workflows/detail.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "workflow": workflow,
            "definition": definition,
            "stages": stages,
            "can_act": can_act,
            "approval_progress": approval_progress,
        }
    )


@router.get("/workflows/{workflow_id}/edit-step", response_class=HTMLResponse)
async def edit_step_form(
    workflow_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not WorkflowEngine.can_user_act(workflow, current_user):
        raise HTTPException(status_code=403, detail="You do not have permission to act on this workflow step.")

    step_def = WorkflowEngine.get_step_def(workflow)
    if not step_def:
        raise HTTPException(status_code=400, detail="Invalid step definition.")

    return templates.TemplateResponse(
        request=request,
        name="workflows/step_edit.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "workflow": workflow,
            "step_def": step_def
        }
    )


@router.post("/workflows/{workflow_id}/edit-step")
async def save_step_edits(
    workflow_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not WorkflowEngine.can_user_act(workflow, current_user):
        raise HTTPException(status_code=403, detail="Permission denied for current step.")

    # Parse dynamic form data
    form_data = await request.form()
    action = form_data.get("action")
    comment = form_data.get("step_comment", "")

    step_payload = {}
    for key, value in form_data.items():
        if key not in ["action", "step_comment"]:
            # On revision, input fields are optional: keep existing data if left blank
            if action in ["REQUEST_REVISION", "REQUEST_CHANGES"] and (value == "" or value is None):
                continue
            step_payload[key] = value

    WorkflowEngine.process_step_action(
        db=db,
        workflow=workflow,
        user=current_user,
        action=action,
        step_payload=step_payload,
        comment=comment
    )

    return RedirectResponse(url=f"/workflows/{workflow.id}", status_code=status.HTTP_302_FOUND)


@router.get("/workflows/{workflow_id}/approve", response_class=HTMLResponse)
async def approve_form(
    workflow_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not WorkflowEngine.can_user_act(workflow, current_user):
        raise HTTPException(status_code=403, detail="You do not have approval permissions for this workflow.")

    definition = WorkflowEngine.get_definition(workflow.workflow_type)
    approval_progress = WorkflowEngine.get_approval_progress(workflow, current_user)

    return templates.TemplateResponse(
        request=request,
        name="workflows/approve.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "workflow": workflow,
            "definition": definition,
            "approval_progress": approval_progress,
        }
    )


@router.post("/workflows/{workflow_id}/approve")
async def process_approval(
    workflow_id: int,
    action: str = Form(...),
    comment: str = Form(...),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not WorkflowEngine.can_user_act(workflow, current_user):
        raise HTTPException(status_code=403, detail="Permission denied for approval.")

    WorkflowEngine.process_step_action(
        db=db,
        workflow=workflow,
        user=current_user,
        action=action,
        step_payload={},
        comment=comment
    )

    return RedirectResponse(url=f"/workflows/{workflow.id}", status_code=status.HTTP_302_FOUND)
