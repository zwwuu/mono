from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class FormField:
    name: str
    label: str
    type: str = "text"  # text, textarea, number, select, date, checkbox
    required: bool = True
    options: List[str] = field(default_factory=list)
    placeholder: str = ""
    help_text: str = ""
    default_value: Any = None


@dataclass
class ReminderConfig:
    """Declares a scheduled reminder that fires when a step becomes active."""
    description: str                       # Human-readable label shown in the UI
    task_type: str = "reminder"            # "reminder" | "escalation"
    recipient_role: str = "assigned"       # role to email, or "requester" / "assigned"
    delay_seconds: Optional[int] = None   # fire X seconds after the step activates
    run_at_iso: Optional[str] = None      # absolute ISO-8601 UTC datetime (takes priority)


@dataclass
class WorkflowStepDef:
    step_id: str
    title: str
    description: str
    assigned_role: str = ""  # primary role: requester, reviewer, approver, admin
    assigned_roles: List[str] = field(default_factory=list)  # multiple allowed roles
    assigned_users: List[str] = field(default_factory=list)  # specific allowed usernames
    is_interactive_form: bool = True
    is_approval_step: bool = False

    # Approver threshold & parallel approval settings
    approvals_required: int = 1  # number of distinct approvals required to advance
    parallel_approval: bool = False  # True when concurrent parallel signoffs are active
    required_role_approvals: Dict[str, int] = field(default_factory=dict)  # e.g. {"approver": 1, "reviewer": 1}
    required_user_approvals: List[str] = field(default_factory=list)  # e.g. ["bob.manager", "alice.dev"]

    fields: List[FormField] = field(default_factory=list)
    # Mapping of action -> (next_step_id, next_status, next_assigned_role)
    transitions: Dict[str, tuple] = field(default_factory=dict)
    # Optional scheduled reminders that activate when this step becomes active
    reminders: List[ReminderConfig] = field(default_factory=list)

    def is_multi_approver(self) -> bool:
        """Returns True if this step requires more than 1 approval or has parallel constraints."""
        return (
            self.parallel_approval or
            self.approvals_required > 1 or
            bool(self.required_role_approvals) or
            bool(self.required_user_approvals)
        )

    def get_eligible_roles(self) -> List[str]:
        """Returns all roles authorized to act on this step."""
        roles = list(self.assigned_roles)
        if self.assigned_role and self.assigned_role not in roles:
            roles.append(self.assigned_role)
        for r in self.required_role_approvals.keys():
            if r not in roles:
                roles.append(r)
        return roles

    def get_eligible_users(self, workflow=None) -> List[str]:
        """Returns specific usernames authorized to act on this step."""
        users = list(self.assigned_users)
        for u in self.required_user_approvals:
            if u not in users:
                users.append(u)
        if workflow:
            # Check dynamic assigned user on the workflow instance
            if getattr(workflow, "assigned_user", None) and workflow.assigned_user.username:
                if workflow.assigned_user.username not in users:
                    users.append(workflow.assigned_user.username)
            # Check form_data for dynamically nominated approver
            form_approver = workflow.form_data.get("assigned_approver") or workflow.form_data.get("specific_approver")
            if form_approver and form_approver not in users:
                users.append(form_approver)
        return users



@dataclass
class WorkflowRole:
    """Represents a feature-specific role tailored to a particular workflow."""
    code: str         # e.g. "dept_head", "security_officer", "procurement_specialist"
    name: str         # Human-friendly title
    description: str = ""  # Role scope and responsibilities


@dataclass
class WorkflowDefinition:
    type_id: str
    title: str
    description: str
    icon_badge: str
    initial_step: str
    steps: Dict[str, WorkflowStepDef]
    step_order: List[str] = field(default_factory=list)
    # Feature roles specific to this workflow type
    feature_roles: List[WorkflowRole] = field(default_factory=list)

    def __post_init__(self):
        if not self.step_order:
            # Auto-infer order from initial step and primary transitions
            order = []
            visited = set()
            curr = self.initial_step
            while curr and curr in self.steps and curr not in visited:
                order.append(curr)
                visited.add(curr)
                transitions = self.steps[curr].transitions
                next_step = None
                for act in ["SUBMIT", "UPDATE_STEP", "CONFIRM_AND_SUBMIT", "APPROVE"]:
                    if act in transitions:
                        next_step = transitions[act][0]
                        break
                curr = next_step
            self.step_order = order

    def get_step(self, step_id: str) -> Optional[WorkflowStepDef]:
        return self.steps.get(step_id)

    def get_role_codes(self) -> List[str]:
        return [r.code for r in self.feature_roles]

    def get_role_label(self, code: str) -> str:
        for r in self.feature_roles:
            if r.code == code:
                return r.name
        return code.replace("_", " ").title()


    def get_field(self, field_name: str) -> Optional[FormField]:
        for step in self.steps.values():
            for f in step.fields:
                if f.name == field_name:
                    return f
        return None

    def get_progress_stages(self, current_step_id: str, status: str) -> List[Dict[str, Any]]:
        """Returns ordered progress stages for visual stepper."""
        stages = []
        is_completed = status in ["APPROVED", "CONFIRMED", "COMPLETED"]
        is_rejected = status in ["REJECTED", "CANCELLED"]

        current_idx = -1
        if current_step_id in self.step_order:
            current_idx = self.step_order.index(current_step_id)

        for idx, step_id in enumerate(self.step_order):
            step = self.steps.get(step_id)
            if not step:
                continue

            if is_completed:
                stage_status = "completed"
            elif is_rejected and step_id == current_step_id:
                stage_status = "rejected"
            elif current_idx > idx:
                stage_status = "completed"
            elif current_idx == idx:
                stage_status = "active"
            else:
                stage_status = "upcoming"

            stages.append({
                "step_id": step_id,
                "title": step.title.split(":")[-1].strip() if ":" in step.title else step.title,
                "full_title": step.title,
                "role": step.assigned_role,
                "status": stage_status,
                "is_current": current_step_id == step_id
            })

        return stages
