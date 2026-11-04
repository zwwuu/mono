from apps.workflow_app.workflows.base import (
    FormField,
    WorkflowDefinition,
    WorkflowRole,
    WorkflowStepDef,
)

IT_ACCESS_WORKFLOW = WorkflowDefinition(
    type_id="it_access_request",
    title="IT & System Access Request",
    description="Multi-step access request: user specifies systems & role -> security officer reviews permissions -> system owner signs off.",
    icon_badge="Security Access",
    initial_step="step_1_request",
    step_order=["step_1_request", "step_2_security_audit", "step_3_owner_approval", "completed"],
    feature_roles=[
        WorkflowRole(code="security_officer", name="Information Security & Audit Officer", description="Verifies MFA, identity compliance, and least-privilege standards."),
        WorkflowRole(code="system_owner", name="Application & System Business Owner", description="Grants business authorization for the system access grant.")
    ],
    steps={
        "step_1_request": WorkflowStepDef(
            step_id="step_1_request",
            title="Step 1: Access Requirement & Scope",
            description="Define the target system, environment, access role, and justification.",
            assigned_role="requester",
            fields=[
                FormField(name="target_system", label="Target System / Application", type="select", options=["Production Kubernetes Cluster", "Customer Database (Read-Only)", "CI/CD Deployment Pipelines", "Cloud Infrastructure (AWS/GCP)", "ERP Finance Suite"], required=True),
                FormField(name="access_level", label="Requested Access Level", type="select", options=["Read-Only Auditor", "Operator / Deployment", "Administrator / Privileged", "Custom Role"], required=True),
                FormField(name="access_duration", label="Access Duration", type="select", options=["24 Hours (Emergency)", "30 Days (Project)", "90 Days", "Permanent (Job Role)"], required=True),
                FormField(name="business_justification", label="Business Need & Reason for Access", type="textarea", required=True, placeholder="Explain ticket ID, project name, or customer reason requiring these privileges.")
            ],
            transitions={
                "SUBMIT": ("step_2_security_audit", "PENDING_REVIEW", "security_officer"),
                "SAVE_DRAFT": ("step_1_request", "DRAFT", "requester")
            }
        ),
        "step_2_security_audit": WorkflowStepDef(
            step_id="step_2_security_audit",
            title="Step 2: Security & Compliance Audit",
            description="Security team verifies MFA, background clearance, and least-privilege compliance.",
            assigned_role="security_officer",
            assigned_roles=["security_officer", "reviewer"],
            fields=[
                FormField(name="identity_clearance", label="Identity & Background Verification", type="select", options=["Verified & Cleared", "Conditional Clearance", "Requires Supervisor Verification"], required=True),
                FormField(name="assigned_group_dn", label="Target LDAP/IAM Group Assigned", type="text", required=True, placeholder="cn=prod-k8s-operators,ou=security,dc=example,dc=com"),
                FormField(name="security_notes", label="Security & Risk Assessment Notes", type="textarea", required=True, placeholder="Confirmed 2FA is active; no conflicting separation-of-duty policies found.")
            ],
            transitions={
                "UPDATE_STEP": ("step_3_owner_approval", "PENDING_APPROVAL", "system_owner"),
                "REQUEST_REVISION": ("step_1_request", "REVISION_REQUESTED", "requester")
            }
        ),
        "step_3_owner_approval": WorkflowStepDef(
            step_id="step_3_owner_approval",
            title="Step 3: System Owner Sign-off",
            description="System Owner grants or denies permission for the access grant.",
            assigned_role="system_owner",
            assigned_roles=["system_owner", "approver"],
            is_approval_step=True,
            fields=[],
            transitions={
                "APPROVE": ("completed", "APPROVED", "requester"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_1_request", "REVISION_REQUESTED", "requester")
            }
        ),
        "completed": WorkflowStepDef(
            step_id="completed",
            title="Access Granted & Activated",
            description="Access has been approved and provisioned.",
            assigned_role="admin",
            is_interactive_form=False
        ),
        "rejected": WorkflowStepDef(
            step_id="rejected",
            title="Access Request Denied",
            description="The access request was declined.",
            assigned_role="admin",
            is_interactive_form=False
        )
    }
)
