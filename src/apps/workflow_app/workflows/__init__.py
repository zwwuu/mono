from apps.workflow_app.workflows.base import (
    FormField,
    ReminderConfig,
    WorkflowDefinition,
    WorkflowRole,
    WorkflowStepDef,
)
from apps.workflow_app.workflows.definitions import (
    CAPITAL_PROJECT_WORKFLOW,
    EXPENSE_REIMBURSEMENT_WORKFLOW,
    IT_ACCESS_WORKFLOW,
    PURCHASE_REQUEST_WORKFLOW,
    REMOTE_VPN_WORKFLOW,
)
from apps.workflow_app.workflows.registry import WorkflowRegistry

# Register default built-in workflows
WorkflowRegistry.register(PURCHASE_REQUEST_WORKFLOW)
WorkflowRegistry.register(IT_ACCESS_WORKFLOW)
WorkflowRegistry.register(EXPENSE_REIMBURSEMENT_WORKFLOW)
WorkflowRegistry.register(REMOTE_VPN_WORKFLOW)
WorkflowRegistry.register(CAPITAL_PROJECT_WORKFLOW)

__all__ = [
    "FormField",
    "WorkflowStepDef",
    "WorkflowDefinition",
    "ReminderConfig",
    "WorkflowRole",
    "WorkflowRegistry",
    "PURCHASE_REQUEST_WORKFLOW",
    "IT_ACCESS_WORKFLOW",
    "EXPENSE_REIMBURSEMENT_WORKFLOW",
    "REMOTE_VPN_WORKFLOW",
    "CAPITAL_PROJECT_WORKFLOW",
]

