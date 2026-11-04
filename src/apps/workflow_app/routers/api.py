from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.workflow_app.database import get_db
from apps.workflow_app.dependencies import require_user
from apps.workflow_app.models.user import User
from apps.workflow_app.models.workflow import WorkflowInstance, WorkflowStepHistory
from apps.workflow_app.schemas.user import UserRead
from apps.workflow_app.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowStepActionRequest,
)
from apps.workflow_app.services.mail_service import EmailService
from apps.workflow_app.services.scheduler_service import SchedulerService
from apps.workflow_app.services.workflow_engine import WorkflowEngine

router = APIRouter(prefix="/api", tags=["REST API"])


@router.get("/users/me", response_model=UserRead)
async def get_my_profile(current_user: User = Depends(require_user)):
    return current_user


@router.get("/workflows", response_model=List[WorkflowResponse])
async def list_workflows_api(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user)
):
    workflows = db.query(WorkflowInstance).order_by(WorkflowInstance.updated_at.desc()).all()
    return workflows


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow_api(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


from apps.workflow_app.services.workflow_engine import WorkflowEngine, WorkflowRegistry


@router.post("/workflows", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow_api(
    req: WorkflowCreateRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    definition = WorkflowRegistry.get(req.workflow_type)
    if not definition:
        raise HTTPException(status_code=400, detail=f"Unknown workflow type '{req.workflow_type}'")

    initial_step_def = definition.get_step(definition.initial_step)
    if not initial_step_def:
        raise HTTPException(status_code=400, detail="Invalid initial workflow step definition")

    count = db.query(WorkflowInstance).count() + 1
    year = datetime.now(timezone.utc).year
    prefix = definition.type_id[:3].upper()
    tracking_code = f"{prefix}-{year}-{count:04d}"

    # Merge explicit payload with extra model fields if provided
    payload = dict(req.payload or {})
    extra_fields = req.model_extra or {}
    payload.update(extra_fields)

    # Resolve title
    title = req.title
    if not title:
        first_field_name = initial_step_def.fields[0].name if initial_step_def.fields else None
        first_val = payload.get(first_field_name) if first_field_name else None
        title = f"{definition.title}: {first_val}" if first_val else f"{definition.title} #{count}"

    next_step_id, next_status, next_role = initial_step_def.transitions.get(
        "SUBMIT",
        list(initial_step_def.transitions.values())[0]
    )

    workflow = WorkflowInstance(
        tracking_code=tracking_code,
        title=title,
        workflow_type=req.workflow_type,
        current_step=next_step_id,
        status=next_status,
        requester_id=current_user.id,
        assigned_role=next_role,
        form_data=payload
    )
    db.add(workflow)
    db.flush()

    history = WorkflowStepHistory(
        workflow_id=workflow.id,
        step_name=initial_step_def.title,
        action="SUBMIT",
        actor_id=current_user.id,
        actor_name=current_user.display_name,
        comment="Submitted via REST API",
        step_snapshot=payload
    )
    db.add(history)
    db.commit()
    db.refresh(workflow)

    # Schedule reminders for next step
    SchedulerService.schedule_step_reminders(db, workflow, next_step_id)
    db.commit()

    # Trigger notifications for next assigned role
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

    return workflow


@router.post("/workflows/{workflow_id}/action", response_model=WorkflowResponse)
async def process_workflow_action_api(
    workflow_id: int,
    action_req: WorkflowStepActionRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if not WorkflowEngine.can_user_act(workflow, current_user):
        raise HTTPException(status_code=403, detail="Permission denied to act on current workflow step.")

    try:
        WorkflowEngine.process_step_action(
            db=db,
            workflow=workflow,
            user=current_user,
            action=action_req.action,
            step_payload=action_req.payload,
            comment=action_req.comment or ""
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return workflow
