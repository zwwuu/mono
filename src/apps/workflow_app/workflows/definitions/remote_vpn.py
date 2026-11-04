from app.workflows.base import WorkflowDefinition, WorkflowStepDef, FormField, ReminderConfig, WorkflowRole

REMOTE_VPN_WORKFLOW = WorkflowDefinition(
    type_id="remote_vpn_request",
    title="Remote VPN Access Request",
    description="Multi-step remote access setup: employee fills out device & access request -> department head approves -> IT operations performs technical sign-off and activation.",
    icon_badge="Remote VPN",
    initial_step="step_1_vpn_request",
    step_order=["step_1_vpn_request", "step_2_dept_approval", "step_3_it_signoff", "completed"],
    feature_roles=[
        WorkflowRole(code="dept_head", name="Department Head / Endorser", description="Verifies employee remote work business necessity and team policy."),
        WorkflowRole(code="network_admin", name="Network Operations & Security Lead", description="Provisions gateway IP tunnel, configures MFA, and delivers VPN profile.")
    ],
    steps={
        "step_1_vpn_request": WorkflowStepDef(
            step_id="step_1_vpn_request",
            title="Step 1: VPN Requirements & Device Specification",
            description="Submit your device information, access gateway region, and remote work business justification.",
            assigned_role="requester",
            fields=[
                FormField(name="device_platform", label="Device Operating System & Type", type="select", options=["Corporate Laptop (Windows 11)", "Corporate Laptop (macOS)", "Corporate Workstation (Linux)", "Managed Tablet / Mobile Device"], required=True),
                FormField(name="device_hostname", label="Device Hostname / Asset Tag", type="text", required=True, placeholder="e.g. CORP-NB-8841"),
                FormField(name="gateway_region", label="Preferred VPN Gateway Region", type="select", options=["Americas (US East - N. Virginia)", "Americas (US West - Oregon)", "EMEA (Europe - Frankfurt)", "APAC (Singapore)"], required=True),
                FormField(name="access_scope", label="Required Network Segment / Resources", type="select", options=["General Corporate LAN & Intranet", "Engineering & R&D Development VPC", "Production Cloud Bastion (Restricted)", "Partner / Vendor Isolated Zone"], required=True),
                FormField(name="access_duration", label="Requested Access Duration", type="select", options=["Permanent (Full-time Employee)", "6 Months (Contractor/Vendor)", "30 Days (Temporary Project)", "14 Days (Business Travel)"], required=True),
                FormField(name="business_justification", label="Business Need & Remote Work Purpose", type="textarea", required=True, placeholder="State project responsibilities, remote work location, and internal systems that must be reached.")
            ],
            transitions={
                "SUBMIT": ("step_2_dept_approval", "PENDING_DEPT_APPROVAL", "dept_head"),
                "SAVE_DRAFT": ("step_1_vpn_request", "DRAFT", "requester")
            }
        ),
        "step_2_dept_approval": WorkflowStepDef(
            step_id="step_2_dept_approval",
            title="Step 2: Department Head Endorsement & Approval",
            description="Department head verifies business need, remote work policy adherence, and grants authorization.",
            assigned_role="dept_head",
            assigned_roles=["dept_head", "approver"],
            is_approval_step=True,
            fields=[],
            transitions={
                "APPROVE": ("step_3_it_signoff", "PENDING_IT_SIGNOFF", "network_admin"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_1_vpn_request", "REVISION_REQUESTED", "requester")
            },
            reminders=[
                ReminderConfig(
                    description="Reminder: VPN access request pending department head approval for 24 hours",
                    task_type="reminder",
                    recipient_role="dept_head",
                    delay_seconds=86400,  # 24 hours
                ),
            ]
        ),
        "step_3_it_signoff": WorkflowStepDef(
            step_id="step_3_it_signoff",
            title="Step 3: IT Technical Configuration & Provisioning Sign-off",
            description="IT Operations assigns gateway IP, allocates tunnel subnet, enforces MFA, and signs off for completion.",
            assigned_role="network_admin",
            assigned_roles=["network_admin", "reviewer"],
            fields=[
                FormField(name="assigned_gateway_endpoint", label="Assigned Gateway Hostname / IP", type="text", required=True, placeholder="vpn-gw01.useast.corp.internal"),
                FormField(name="allocated_tunnel_ip", label="Allocated Tunnel IP / Subnet", type="text", required=True, placeholder="10.240.55.102/24"),
                FormField(name="vpn_protocol", label="Configured VPN Protocol", type="select", options=["WireGuard Enterprise VPN", "OpenVPN SSL (TLS 1.3)", "IPsec IKEv2 Suite-B", "Zero-Trust Network Access (ZTNA) Agent"], required=True),
                FormField(name="mfa_method", label="Multi-Factor Authentication (MFA) Method", type="select", options=["FIDO2 Hardware Key (YubiKey)", "Authenticator App (TOTP)", "Duo / Okta Push Notification"], required=True),
                FormField(name="it_signoff_notes", label="IT Completion & Sign-off Notes", type="textarea", required=True, placeholder="Client profile generated, certificate authority signed, and route table provisioned.")
            ],
            transitions={
                "COMPLETE_SIGNOFF": ("completed", "COMPLETED", "requester"),
                "REQUEST_REVISION": ("step_1_vpn_request", "REVISION_REQUESTED", "requester")
            }
        ),
        "completed": WorkflowStepDef(
            step_id="completed",
            title="VPN Provisioned & Active",
            description="VPN profile has been issued and connectivity is active.",
            assigned_role="admin",
            is_interactive_form=False
        ),
        "rejected": WorkflowStepDef(
            step_id="rejected",
            title="VPN Request Rejected",
            description="The VPN request was declined.",
            assigned_role="admin",
            is_interactive_form=False
        )
    }
)
