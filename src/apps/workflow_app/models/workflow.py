import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from common.db import Base


def utc_now():
    return datetime.now(timezone.utc)


class WorkflowInstance(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    tracking_code = Column(String(50), unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False)
    workflow_type = Column(String(100), default="purchase_request", nullable=False)
    
    # State tracking
    current_step = Column(String(100), default="step_1_draft", nullable=False)
    status = Column(String(50), default="DRAFT", nullable=False)  
    # e.g.: DRAFT, PENDING_REVIEW, IN_REVIEW, PENDING_APPROVAL, APPROVED, REJECTED, CONFIRMED
    
    # Responsibility
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_role = Column(String(50), default="requester", nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Multi-step dynamic form data stored as JSON
    form_data_raw = Column("form_data", Text, default="{}", nullable=False)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    requester = relationship("User", foreign_keys=[requester_id], back_populates="requested_workflows")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id], back_populates="assigned_workflows")
    history = relationship("WorkflowStepHistory", back_populates="workflow", order_by="WorkflowStepHistory.created_at.desc()")
    scheduled_tasks = relationship("ScheduledTask", back_populates="workflow", order_by="ScheduledTask.created_at.desc()")
    step_approvals = relationship("StepApproval", back_populates="workflow", order_by="StepApproval.created_at.asc()", cascade="all, delete-orphan")

    @property
    def form_data(self) -> dict:
        try:
            return json.loads(self.form_data_raw or "{}")
        except Exception:
            return {}

    @form_data.setter
    def form_data(self, value: dict):
        self.form_data_raw = json.dumps(value or {})


class WorkflowStepHistory(Base):
    __tablename__ = "workflow_step_history"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    step_name = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)  # SUBMIT, EDIT_STEP, APPROVE, REJECT, REQUEST_REVISION, CONFIRM
    
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_name = Column(String(150), nullable=False)
    
    comment = Column(Text, nullable=True)
    step_snapshot_raw = Column("step_snapshot", Text, default="{}", nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    workflow = relationship("WorkflowInstance", back_populates="history")
    actor = relationship("User", back_populates="step_histories")

    @property
    def step_snapshot(self) -> dict:
        try:
            return json.loads(self.step_snapshot_raw or "{}")
        except Exception:
            return {}

    @step_snapshot.setter
    def step_snapshot(self, value: dict):
        self.step_snapshot_raw = json.dumps(value or {})


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(50), nullable=True)
    actor_username = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ScheduledTask(Base):
    """Tracks reminder/escalation jobs scheduled for workflow instances."""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(100), unique=True, index=True, nullable=False)  # APScheduler job ID
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    workflow_tracking_code = Column(String(50), nullable=False)
    task_type = Column(String(50), nullable=False)  # e.g. "reminder", "escalation"
    description = Column(String(255), nullable=True)  # human-readable label
    run_at = Column(DateTime, nullable=True)       # absolute scheduled time (UTC)
    delay_seconds = Column(Integer, nullable=True)  # delay from creation if run_at not given
    recipient_role = Column(String(50), nullable=True)  # role to notify
    is_fired = Column(Boolean, default=False, nullable=False)
    is_cancelled = Column(Boolean, default=False, nullable=False)
    fired_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationship
    workflow = relationship("WorkflowInstance", back_populates="scheduled_tasks")


class StepApproval(Base):
    """
    Tracks individual sign-off decisions for parallel or multi-approver workflow steps.
    Supports approval by role, by specific user, and parallel approvals.
    """
    __tablename__ = "step_approvals"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    step_id = Column(String(100), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_username = Column(String(100), nullable=False)
    user_display_name = Column(String(150), nullable=False)
    user_role = Column(String(50), nullable=False)

    decision = Column(String(50), nullable=False)  # "APPROVE", "REJECT", "REQUEST_REVISION"
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    workflow = relationship("WorkflowInstance", back_populates="step_approvals")
    user = relationship("User", foreign_keys=[user_id])
