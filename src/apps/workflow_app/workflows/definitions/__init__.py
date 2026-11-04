from apps.workflow_app.workflows.definitions.capital_project import CAPITAL_PROJECT_WORKFLOW
from apps.workflow_app.workflows.definitions.expense_reimbursement import (
    EXPENSE_REIMBURSEMENT_WORKFLOW,
)
from apps.workflow_app.workflows.definitions.it_access import IT_ACCESS_WORKFLOW
from apps.workflow_app.workflows.definitions.purchase_request import PURCHASE_REQUEST_WORKFLOW
from apps.workflow_app.workflows.definitions.remote_vpn import REMOTE_VPN_WORKFLOW

__all__ = [
    "PURCHASE_REQUEST_WORKFLOW",
    "IT_ACCESS_WORKFLOW",
    "EXPENSE_REIMBURSEMENT_WORKFLOW",
    "REMOTE_VPN_WORKFLOW",
    "CAPITAL_PROJECT_WORKFLOW",
]

