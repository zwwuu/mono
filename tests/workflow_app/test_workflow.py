import pytest
from starlette.testclient import TestClient

from apps.workflow_app.database import SessionLocal, init_db
from apps.workflow_app.main import app
from apps.workflow_app.models.workflow import WorkflowInstance
from apps.workflow_app.services.mail_service import DEV_MAILBOX


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    init_db()


def test_login_flow():
    client = TestClient(app, follow_redirects=False)
    
    # 1. Unauthenticated access to dashboard should redirect to /login
    resp = client.get("/dashboard")
    assert resp.status_code == 307 or resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")

    # 2. Login as requester
    login_resp = client.post("/login", data={"username": "requester", "password": "password"})
    assert login_resp.status_code == 302
    assert login_resp.headers["location"] == "/dashboard"

    # 3. Access dashboard with session
    dash_resp = client.get("/dashboard")
    assert dash_resp.status_code == 200
    assert "Workflow Dashboard" in dash_resp.text
    assert "Alice Requester" in dash_resp.text


def test_full_workflow_lifecycle():
    client = TestClient(app, follow_redirects=True)
    DEV_MAILBOX.clear()

    # 1. Login as Requester (Alice)
    client.get("/dev/switch-user?user=requester")

    # 2. Submit initial workflow request (Step 1)
    create_resp = client.post("/workflows/new", data={
        "workflow_type": "purchase_request",
        "title": "NVIDIA H100 GPU Cluster",
        "item_title": "4x H100 SXM5 Nodes",
        "category": "Hardware",
        "estimated_amount": "120000",
        "urgency": "High",
        "business_justification": "Required for LLM fine-tuning and inference pipelines.",
        "action": "SUBMIT"
    })
    assert create_resp.status_code == 200
    assert "NVIDIA H100 GPU Cluster" in create_resp.text
    assert "PENDING_REVIEW" in create_resp.text

    # Extract workflow ID from database
    db = SessionLocal()
    wf = db.query(WorkflowInstance).filter(WorkflowInstance.title == "NVIDIA H100 GPU Cluster").order_by(WorkflowInstance.id.desc()).first()
    assert wf is not None
    wf_id = wf.id
    db.close()

    # 3. Verify notification email was generated for Reviewer
    assert len(DEV_MAILBOX) >= 1
    assert any("Action Required" in m["subject"] for m in DEV_MAILBOX)
    assert any("bob.reviewer@example.com" in m["to"] for m in DEV_MAILBOX)

    # 4. Switch to Reviewer (Bob) and perform Multi-Step Edit (Step 2)
    client.get("/dev/switch-user?user=reviewer")
    
    step2_resp = client.post(f"/workflows/{wf_id}/edit-step", data={
        "vendor_name": "Supermicro Enterprise Systems",
        "gl_account_code": "GL-9820-AI",
        "final_quoted_price": "115000",
        "sla_warranty_terms": "4-year 24/7 on-site mission critical replacement",
        "technical_notes": "Passed technical security inspection, power supply verified for rack 12.",
        "step_comment": "Negotiated 5,000 discount and verified power/cooling specifications.",
        "action": "UPDATE_STEP"
    })
    assert step2_resp.status_code == 200
    assert "Supermicro Enterprise Systems" in step2_resp.text

    # 5. Reviewer confirms and advances to Executive Approval (Step 3)
    step3_resp = client.post(f"/workflows/{wf_id}/edit-step", data={
        "reviewer_signoff_notes": "All technical and pricing diligence completed. Recommended for executive signoff.",
        "step_comment": "Submitted for executive approval.",
        "action": "CONFIRM_AND_SUBMIT"
    })
    assert step3_resp.status_code == 200
    assert "PENDING_APPROVAL" in step3_resp.text

    # 6. Switch to Approver (Carol) and Approve (Step 4)
    client.get("/dev/switch-user?user=approver")

    approve_resp = client.post(f"/workflows/{wf_id}/approve", data={
        "action": "APPROVE",
        "comment": "Approved under Q4 AI Infrastructure Capital Budget."
    })
    assert approve_resp.status_code == 200
    assert "APPROVED" in approve_resp.text

    # 7. Verify Approval Notification email sent to requester
    approval_emails = [m for m in DEV_MAILBOX if "APPROVED" in m["subject"]]
    assert len(approval_emails) >= 1
    assert "alice.requester@example.com" in approval_emails[0]["to"]


def test_rest_api():
    client = TestClient(app, follow_redirects=True)
    # Login as admin
    client.get("/dev/switch-user?user=admin")

    # List workflows
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Current user profile
    me_resp = client.get("/api/users/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "admin"


def test_return_for_revision_with_empty_inputs():
    client = TestClient(app, follow_redirects=True)

    # 1. Requester creates request
    client.get("/dev/switch-user?user=requester")
    client.post("/workflows/new", data={
        "workflow_type": "purchase_request",
        "title": "Software License Renewal",
        "item_title": "Enterprise Cloud Seats",
        "category": "Software & Cloud",
        "estimated_amount": "5000",
        "urgency": "Normal",
        "business_justification": "Annual renewal for engineering tooling.",
        "action": "SUBMIT"
    })

    db = SessionLocal()
    wf = db.query(WorkflowInstance).filter(WorkflowInstance.title == "Software License Renewal").order_by(WorkflowInstance.id.desc()).first()
    assert wf is not None
    wf_id = wf.id
    db.close()

    # 2. Reviewer inspects and decides to Return for Revision without filling in Step 2 inputs
    client.get("/dev/switch-user?user=reviewer")
    rev_resp = client.post(f"/workflows/{wf_id}/edit-step", data={
        "vendor_name": "",
        "gl_account_code": "",
        "final_quoted_price": "",
        "sla_warranty_terms": "",
        "technical_notes": "",
        "step_comment": "Please obtain quotes for 50 seats instead of 100 before technical signoff.",
        "action": "REQUEST_REVISION"
    })

    assert rev_resp.status_code == 200
    assert "REVISION_REQUESTED" in rev_resp.text

    # Verify status in database
    db = SessionLocal()
    updated_wf = db.query(WorkflowInstance).filter(WorkflowInstance.id == wf_id).first()
    assert updated_wf.status == "REVISION_REQUESTED"
    assert updated_wf.current_step == "step_1_draft"
    assert updated_wf.assigned_role == "requester"
    db.close()


def test_generic_it_access_workflow():
    client = TestClient(app, follow_redirects=True)

    # 1. Requester submits an IT Access Request
    client.get("/dev/switch-user?user=requester")
    create_resp = client.post("/workflows/new", data={
        "workflow_type": "it_access_request",
        "title": "Prod K8s Admin Access",
        "target_system": "Production Kubernetes Cluster",
        "access_level": "Administrator / Privileged",
        "access_duration": "30 Days (Project)",
        "business_justification": "Required for disaster recovery drill and cluster upgrade.",
        "action": "SUBMIT"
    })
    assert create_resp.status_code == 200

    db = SessionLocal()
    wf = db.query(WorkflowInstance).filter(WorkflowInstance.title == "Prod K8s Admin Access").order_by(WorkflowInstance.id.desc()).first()
    assert wf is not None
    assert wf.workflow_type == "it_access_request"
    assert wf.current_step == "step_2_security_audit"
    assert wf.assigned_role in ["security_officer", "reviewer"]
    wf_id = wf.id
    db.close()

    # 2. Security Reviewer conducts audit (Step 2)
    client.get("/dev/switch-user?user=reviewer")
    step2_resp = client.post(f"/workflows/{wf_id}/edit-step", data={
        "identity_clearance": "Verified & Cleared",
        "assigned_group_dn": "cn=prod-k8s-admins,ou=security,dc=example,dc=com",
        "security_notes": "MFA verified, hardware token active. Approved for 30 days.",
        "step_comment": "Cleared security baseline.",
        "action": "UPDATE_STEP"
    })
    assert step2_resp.status_code == 200

    db = SessionLocal()
    wf2 = db.query(WorkflowInstance).filter(WorkflowInstance.id == wf_id).first()
    assert wf2.current_step == "step_3_owner_approval"
    assert wf2.assigned_role in ["system_owner", "approver"]
    db.close()

    # 3. System Owner Approver signs off (Step 3)
    client.get("/dev/switch-user?user=approver")
    approve_resp = client.post(f"/workflows/{wf_id}/approve", data={
        "action": "APPROVE",
        "comment": "Access authorized for DR drill."
    })
    assert approve_resp.status_code == 200

    db = SessionLocal()
    wf3 = db.query(WorkflowInstance).filter(WorkflowInstance.id == wf_id).first()
    assert wf3.status == "APPROVED"
    assert wf3.current_step == "completed"
    db.close()


def test_remote_vpn_request_workflow():
    client = TestClient(app, follow_redirects=True)
    DEV_MAILBOX.clear()

    # 1. Requester (Alice) submits Remote VPN Request
    client.get("/dev/switch-user?user=requester")
    create_resp = client.post("/workflows/new", data={
        "workflow_type": "remote_vpn_request",
        "title": "Remote Work VPN Access - Alice",
        "device_platform": "Corporate Laptop (macOS)",
        "device_hostname": "CORP-MAC-9821",
        "gateway_region": "Americas (US East - N. Virginia)",
        "access_scope": "Engineering & R&D Development VPC",
        "access_duration": "Permanent (Full-time Employee)",
        "business_justification": "Full-time remote engineering role requiring access to dev cluster and staging databases.",
        "action": "SUBMIT"
    })
    assert create_resp.status_code == 200

    db = SessionLocal()
    wf = db.query(WorkflowInstance).filter(WorkflowInstance.title == "Remote Work VPN Access - Alice").order_by(WorkflowInstance.id.desc()).first()
    assert wf is not None
    assert wf.workflow_type == "remote_vpn_request"
    assert wf.current_step == "step_2_dept_approval"
    assert wf.assigned_role in ["dept_head", "approver"]
    wf_id = wf.id
    db.close()

    # 2. Dept Head (Carol - approver role) reviews and approves
    client.get("/dev/switch-user?user=approver")
    dept_appr_resp = client.post(f"/workflows/{wf_id}/approve", data={
        "action": "APPROVE",
        "comment": "Endorsed for remote engineering duties."
    })
    assert dept_appr_resp.status_code == 200

    db = SessionLocal()
    wf_step2 = db.query(WorkflowInstance).filter(WorkflowInstance.id == wf_id).first()
    assert wf_step2.current_step == "step_3_it_signoff"
    assert wf_step2.assigned_role in ["network_admin", "reviewer"]
    db.close()

    # 3. IT Operations (Bob - reviewer role) configures & performs completion sign-off
    client.get("/dev/switch-user?user=reviewer")
    it_resp = client.post(f"/workflows/{wf_id}/edit-step", data={
        "assigned_gateway_endpoint": "vpn-gw01.useast.corp.internal",
        "allocated_tunnel_ip": "10.240.55.142/24",
        "vpn_protocol": "WireGuard Enterprise VPN",
        "mfa_method": "FIDO2 Hardware Key (YubiKey)",
        "it_signoff_notes": "Routing policy configured. User certificate issued and synced with LDAP directory.",
        "step_comment": "Technical configuration completed and client profile provisioned.",
        "action": "COMPLETE_SIGNOFF"
    })
    assert it_resp.status_code == 200

    db = SessionLocal()
    wf_final = db.query(WorkflowInstance).filter(WorkflowInstance.id == wf_id).first()
    assert wf_final.status == "COMPLETED"
    assert wf_final.current_step == "completed"
    assert wf_final.form_data.get("allocated_tunnel_ip") == "10.240.55.142/24"
    db.close()
