"""
workflows/definitions/capital_project.py
----------------------------------------
Capital Project & Cloud Infrastructure Authorization Workflow.
Demonstrates:
- Parallel multi-approver sign-offs from different roles (approver + reviewer).
- Specific user nomination / approval by designated user.
- Approver amount threshold needed (2 distinct approvals required).
"""

from app.workflows.base import WorkflowDefinition, WorkflowStepDef, FormField, ReminderConfig, WorkflowRole

CAPITAL_PROJECT_WORKFLOW = WorkflowDefinition(
    type_id="capital_project",
    title="Capital Project & Cloud Budget Authorization",
    description="High-value project request requiring concurrent parallel sign-offs from both Department Head and Finance/Compliance leads, plus optional specific named approvers.",
    icon_badge="Capital Budget",
    initial_step="step_1_proposal",
    step_order=["step_1_proposal", "step_2_parallel_approval", "completed"],
    feature_roles=[
        WorkflowRole(code="capex_lead", name="Capital Investment Committee Lead", description="Verifies strategic business alignment and executive project sponsorship."),
        WorkflowRole(code="finance_controller", name="Corporate Finance Controller", description="Validates capital expenditure budget allocations and depreciation schedule.")
    ],
    steps={
        "step_1_proposal": WorkflowStepDef(
            step_id="step_1_proposal",
            title="Step 1: Project Scope, Cost & Approver Designation",
            description="Requester submits project architecture, budget estimate, and can nominate a specific reviewer/approver user.",
            assigned_role="requester",
            fields=[
                FormField(
                    name="project_title",
                    label="Project Initiative Name",
                    type="text",
                    required=True,
                    placeholder="e.g. Q4 Cloud Data Lake & Analytics Platform"
                ),
                FormField(
                    name="capex_amount",
                    label="Estimated Capital Expenditure ($ USD)",
                    type="number",
                    required=True,
                    placeholder="85000"
                ),
                FormField(
                    name="business_unit",
                    label="Business Unit / Cost Center",
                    type="select",
                    options=["Infrastructure & Cloud Ops", "Enterprise AI & Data", "Product Engineering", "Cybersecurity"],
                    required=True
                ),
                FormField(
                    name="strategic_roi",
                    label="Strategic Objective & Projected ROI",
                    type="textarea",
                    required=True,
                    placeholder="Describe how this capital project enhances business capabilities, expected timeline, and cost savings."
                ),
                FormField(
                    name="specific_approver",
                    label="Nominated Lead Approver (Optional Username)",
                    type="text",
                    required=False,
                    placeholder="e.g. bob.manager or leave blank for any authorized approver",
                    help_text="If provided, this specific user is explicitly authorized alongside the role-based approvers."
                ),
            ],
            transitions={
                "SUBMIT": ("step_2_parallel_approval", "PENDING_APPROVAL", "capex_lead"),
                "SAVE_DRAFT": ("step_1_proposal", "DRAFT", "requester")
            }
        ),
        "step_2_parallel_approval": WorkflowStepDef(
            step_id="step_2_parallel_approval",
            title="Step 2: Dual Parallel Review & Executive Sign-off",
            description="Requires concurrent independent sign-offs from both the Capital Committee Lead AND the Corporate Finance Controller.",
            is_approval_step=True,
            parallel_approval=True,
            # Amount of approvers needed
            approvals_required=2,
            # Roles authorized to participate
            assigned_roles=["capex_lead", "finance_controller", "approver", "reviewer"],
            assigned_role="capex_lead",
            # Parallel quota: requires 1 sign-off from capex_lead AND 1 from finance_controller
            required_role_approvals={
                "capex_lead": 1,
                "finance_controller": 1
            },
            fields=[],
            transitions={
                "APPROVE": ("completed", "APPROVED", "requester"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_1_proposal", "REVISION_REQUESTED", "requester")
            },
            reminders=[
                ReminderConfig(
                    description="Reminder: Dual parallel executive sign-off pending for 24 hours",
                    task_type="reminder",
                    recipient_role="approver",
                    delay_seconds=86400,
                ),
                ReminderConfig(
                    description="Reminder: Finance / Compliance verification sign-off pending for 24 hours",
                    task_type="reminder",
                    recipient_role="reviewer",
                    delay_seconds=86400,
                ),
            ]
        ),
        "completed": WorkflowStepDef(
            step_id="completed",
            title="Capital Project Authorized & Provisioned",
            description="All parallel executive approvals have been received. Project budget is unlocked.",
            assigned_role="admin",
            is_interactive_form=False
        ),
        "rejected": WorkflowStepDef(
            step_id="rejected",
            title="Capital Project Declined",
            description="The capital expenditure was declined during parallel executive review.",
            assigned_role="admin",
            is_interactive_form=False
        )
    }
)
