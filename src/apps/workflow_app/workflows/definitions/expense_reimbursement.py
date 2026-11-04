from app.workflows.base import WorkflowDefinition, WorkflowStepDef, FormField, WorkflowRole

EXPENSE_REIMBURSEMENT_WORKFLOW = WorkflowDefinition(
    type_id="expense_reimbursement",
    title="Expense & Travel Reimbursement",
    description="Multi-step employee expense claim: receipt submission -> finance audit & tax check -> director approval.",
    icon_badge="Finance",
    initial_step="step_1_claim",
    step_order=["step_1_claim", "step_2_finance_audit", "step_3_director_approval", "completed"],
    feature_roles=[
        WorkflowRole(code="finance_auditor", name="Finance & Travel Policy Auditor", description="Verifies receipts, per diem thresholds, and tax compliance."),
        WorkflowRole(code="finance_director", name="Finance Director / Payment Release", description="Authorizes corporate treasury release and payment queue.")
    ],
    steps={
        "step_1_claim": WorkflowStepDef(
            step_id="step_1_claim",
            title="Step 1: Expense Report & Receipts",
            description="Submit trip details, dates, expense categories, and claim amount.",
            assigned_role="requester",
            fields=[
                FormField(name="expense_title", label="Expense Report Purpose / Event", type="text", required=True, placeholder="e.g. Q3 Client Onsite Visit - Chicago"),
                FormField(name="expense_type", label="Primary Category", type="select", options=["Travel & Airfare", "Hotel & Lodging", "Meals & Entertainment", "Client Meeting", "Conference & Training", "Office Equipment"], required=True),
                FormField(name="total_amount", label="Total Claim Amount ($)", type="number", required=True, placeholder="1420.50"),
                FormField(name="expense_date", label="Date of Incurrence", type="date", required=True),
                FormField(name="business_justification", label="Business Purpose & Project Code", type="textarea", required=True, placeholder="Client onsite deployment and contract kickoff meetings.")
            ],
            transitions={
                "SUBMIT": ("step_2_finance_audit", "PENDING_REVIEW", "finance_auditor"),
                "SAVE_DRAFT": ("step_1_claim", "DRAFT", "requester")
            }
        ),
        "step_2_finance_audit": WorkflowStepDef(
            step_id="step_2_finance_audit",
            title="Step 2: Finance & Policy Audit",
            description="Finance audits receipts, verifies policy per-diem rates, and assigns cost center.",
            assigned_role="finance_auditor",
            assigned_roles=["finance_auditor", "reviewer"],
            fields=[
                FormField(name="cost_center", label="Chargeable Cost Center / Project Code", type="text", required=True, placeholder="CC-5020-OPERATIONS"),
                FormField(name="tax_deductible_amount", label="Tax Deductible Portion ($)", type="number", required=True, placeholder="1420.50"),
                FormField(name="policy_compliance", label="Company Policy Check", type="select", options=["Fully Compliant with Receipts", "Minor Exception Approved", "Per Diem Rate Applied"], required=True),
                FormField(name="audit_notes", label="Finance Auditor Comments", type="textarea", required=False, placeholder="All electronic receipts verified against card statements.")
            ],
            transitions={
                "UPDATE_STEP": ("step_3_director_approval", "PENDING_APPROVAL", "finance_director"),
                "REQUEST_REVISION": ("step_1_claim", "REVISION_REQUESTED", "requester")
            }
        ),
        "step_3_director_approval": WorkflowStepDef(
            step_id="step_3_director_approval",
            title="Step 3: Director Sign-off & Reimbursement Release",
            description="Department Director approves payment release.",
            assigned_role="finance_director",
            assigned_roles=["finance_director", "approver"],
            is_approval_step=True,
            fields=[],
            transitions={
                "APPROVE": ("completed", "APPROVED", "requester"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_1_claim", "REVISION_REQUESTED", "requester")
            }
        ),
        "completed": WorkflowStepDef(
            step_id="completed",
            title="Reimbursement Approved & Queued for Payment",
            description="Expense claim has been verified and approved.",
            assigned_role="admin",
            is_interactive_form=False
        ),
        "rejected": WorkflowStepDef(
            step_id="rejected",
            title="Reimbursement Claim Rejected",
            description="The expense claim was rejected.",
            assigned_role="admin",
            is_interactive_form=False
        )
    }
)
