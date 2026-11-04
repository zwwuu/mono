from app.workflows.base import WorkflowDefinition, WorkflowStepDef, FormField, ReminderConfig, WorkflowRole

PURCHASE_REQUEST_WORKFLOW = WorkflowDefinition(
    type_id="purchase_request",
    title="Procurement & Purchase Request",
    description="Multi-step procurement workflow: requester submission -> technical/budget enrichment -> reviewer verification -> executive signoff.",
    icon_badge="Procurement",
    initial_step="step_1_draft",
    step_order=["step_1_draft", "step_2_technical", "step_3_review", "step_4_approval", "completed"],
    feature_roles=[
        WorkflowRole(code="procurement_specialist", name="Procurement & Vendor Specialist", description="Enriches request with approved vendors, GL codes, and quoted prices."),
        WorkflowRole(code="budget_reviewer", name="Budget & Account Reviewer", description="Conducts pre-approval verification and executive brief."),
        WorkflowRole(code="executive_approver", name="Executive Approver / VP", description="Grants binding financial authorization.")
    ],
    steps={
        "step_1_draft": WorkflowStepDef(
            step_id="step_1_draft",
            title="Step 1: Request Overview & Justification",
            description="Provide initial item details, estimated cost, and business need.",
            assigned_role="requester",
            fields=[
                FormField(name="item_title", label="Item or Service Name", type="text", required=True, placeholder="e.g. GPU Server Cluster or Enterprise Software License"),
                FormField(name="category", label="Category", type="select", options=["Hardware", "Software & Cloud", "Professional Services", "Office Equipment"]),
                FormField(name="estimated_amount", label="Estimated Cost (USD)", type="number", required=True, placeholder="5000"),
                FormField(name="urgency", label="Urgency Level", type="select", options=["Normal", "High", "Critical"]),
                FormField(name="business_justification", label="Business Need / Justification", type="textarea", required=True, placeholder="Explain why this purchase is essential for the project or department.")
            ],
            transitions={
                "SUBMIT": ("step_2_technical", "PENDING_REVIEW", "procurement_specialist"),
                "SAVE_DRAFT": ("step_1_draft", "DRAFT", "requester")
            }
        ),
        "step_2_technical": WorkflowStepDef(
            step_id="step_2_technical",
            title="Step 2: Technical & Budget Specification",
            description="Reviewer fills in vendor quotes, account codes, and technical compatibility.",
            assigned_role="procurement_specialist",
            assigned_roles=["procurement_specialist", "reviewer"],
            fields=[
                FormField(name="vendor_name", label="Recommended Vendor / Supplier", type="text", required=True, placeholder="e.g. Dell Enterprise or AWS"),
                FormField(name="gl_account_code", label="General Ledger (GL) Code", type="text", required=True, placeholder="e.g. GL-7410-ENG"),
                FormField(name="final_quoted_price", label="Negotiated / Quoted Price ($)", type="number", required=True, placeholder="4850"),
                FormField(name="sla_warranty_terms", label="SLA & Warranty Terms", type="text", required=False, placeholder="3-year next business day support"),
                FormField(name="technical_notes", label="Technical & Security Assessment", type="textarea", required=True, placeholder="Details on security review, compatibility, and compliance checks.")
            ],
            transitions={
                "UPDATE_STEP": ("step_3_review", "PENDING_CONFIRMATION", "budget_reviewer"),
                "REQUEST_REVISION": ("step_1_draft", "REVISION_REQUESTED", "requester")
            },
            reminders=[
                ReminderConfig(
                    description="Reminder: Technical review has been pending for 24 hours",
                    task_type="reminder",
                    recipient_role="procurement_specialist",
                    delay_seconds=86400,  # 24 hours
                ),
                ReminderConfig(
                    description="Escalation: Technical review overdue by 48 hours – please action",
                    task_type="escalation",
                    recipient_role="procurement_specialist",
                    delay_seconds=172800,  # 48 hours
                ),
            ]
        ),
        "step_3_review": WorkflowStepDef(
            step_id="step_3_review",
            title="Step 3: Review & Submission for Approval",
            description="Verify all collected information before sending to executive leadership.",
            assigned_role="budget_reviewer",
            assigned_roles=["budget_reviewer", "reviewer"],
            fields=[
                FormField(name="reviewer_signoff_notes", label="Reviewer Notes / Recommendation", type="textarea", required=True, placeholder="Summary of why this request is ready for executive approval.")
            ],
            transitions={
                "CONFIRM_AND_SUBMIT": ("step_4_approval", "PENDING_APPROVAL", "executive_approver"),
                "RETURN_TO_STEP_2": ("step_2_technical", "IN_REVIEW", "procurement_specialist")
            }
        ),
        "step_4_approval": WorkflowStepDef(
            step_id="step_4_approval",
            title="Step 4: Executive Decision & Sign-off",
            description="Final executive approval, rejection, or request for further modification.",
            assigned_role="executive_approver",
            assigned_roles=["executive_approver", "approver"],
            is_approval_step=True,
            fields=[],
            transitions={
                "APPROVE": ("completed", "APPROVED", "requester"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_2_technical", "REVISION_REQUESTED", "procurement_specialist")
            },
            reminders=[
                ReminderConfig(
                    description="Reminder: Executive approval has been awaited for 48 hours",
                    task_type="reminder",
                    recipient_role="executive_approver",
                    delay_seconds=172800,  # 48 hours
                ),
            ]
        ),
        "completed": WorkflowStepDef(
            step_id="completed",
            title="Workflow Completed & Confirmed",
            description="The workflow has completed all stages and is approved.",
            assigned_role="admin",
            is_interactive_form=False
        ),
        "rejected": WorkflowStepDef(
            step_id="rejected",
            title="Workflow Rejected",
            description="The request was rejected during the approval phase.",
            assigned_role="admin",
            is_interactive_form=False
        )
    }
)
