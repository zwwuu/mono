from app.workflows.base import FormField, WorkflowStepDef, WorkflowDefinition, ReminderConfig, WorkflowRole
from app.workflows.registry import WorkflowRegistry
from app.workflows.definitions import (
    PURCHASE_REQUEST_WORKFLOW,
    IT_ACCESS_WORKFLOW,
    EXPENSE_REIMBURSEMENT_WORKFLOW,
    REMOTE_VPN_WORKFLOW,
    CAPITAL_PROJECT_WORKFLOW,
)

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

