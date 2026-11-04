import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from common.db import Base


def utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    display_name = Column(String(150), nullable=False)
    email = Column(String(255), index=True, nullable=False)
    
    # Primary/default system role: requester, reviewer, approver, admin
    role = Column(String(50), default="requester", nullable=False)
    
    # Flexible multi-role support (e.g. ["procurement_specialist", "dept_head", "security_officer"])
    roles_raw = Column("roles", Text, default="[]", nullable=False)
    
    # Workflow-specific scoped roles mapping (e.g. {"remote_vpn_request": ["dept_head"], "it_access": ["system_owner"]})
    workflow_roles_raw = Column("workflow_roles", Text, default="{}", nullable=False)

    department = Column(String(100), default="General", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    requested_workflows = relationship("WorkflowInstance", foreign_keys="WorkflowInstance.requester_id", back_populates="requester")
    assigned_workflows = relationship("WorkflowInstance", foreign_keys="WorkflowInstance.assigned_user_id", back_populates="assigned_user")
    step_histories = relationship("WorkflowStepHistory", back_populates="actor")

    @property
    def roles(self) -> list[str]:
        """Returns the list of global/feature roles assigned to this user."""
        try:
            items = json.loads(self.roles_raw or "[]")
            if isinstance(items, list):
                # Ensure primary role is included if not present
                all_roles = [self.role] if self.role else []
                for r in items:
                    if r and r not in all_roles:
                        all_roles.append(r)
                return all_roles
        except Exception:
            pass
        return [self.role] if self.role else ["requester"]

    @roles.setter
    def roles(self, value: list[str]):
        clean = list(dict.fromkeys(r.strip() for r in value if r and isinstance(r, str)))
        self.roles_raw = json.dumps(clean)

    @property
    def workflow_roles(self) -> dict[str, list[str]]:
        """Returns dictionary of workflow-specific feature roles."""
        try:
            data = json.loads(self.workflow_roles_raw or "{}")
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    @workflow_roles.setter
    def workflow_roles(self, value: dict[str, list[str]]):
        self.workflow_roles_raw = json.dumps(value or {})

    def has_role(self, target_role: str, workflow_type: str | None = None) -> bool:
        """
        Determines whether the user possesses the target role globally,
        within their multi-roles list, or within the specific workflow context.
        Admins automatically satisfy all role checks.
        """
        if not target_role:
            return True
        if self.role == "admin" or "admin" in self.roles:
            return True
        if target_role == self.role:
            return True
        if target_role in self.roles:
            return True
        if workflow_type and workflow_type in self.workflow_roles:
            if target_role in self.workflow_roles[workflow_type]:
                return True
        return False

    def get_roles_for_workflow(self, workflow_type: str | None = None) -> list[str]:
        """Returns all effective roles held by this user for the specified workflow."""
        roles = list(self.roles)
        if workflow_type and workflow_type in self.workflow_roles:
            for r in self.workflow_roles[workflow_type]:
                if r not in roles:
                    roles.append(r)
        return roles
