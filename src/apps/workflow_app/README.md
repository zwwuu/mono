# Interactive Human-in-the-Loop Workflow Portal

A modern, maintainable FastAPI web application designed for human interactive workflows (HTML forms that require approvals, multi-step edits, and confirmation sign-offs), corporate LDAP authentication, automated mailing notifications, and flexible database support (**SQLite** for local development and **MySQL** for production).

---

## 🌟 Key Features

1. **Human-in-the-Loop Workflows**:
   - Built-in multi-step templates:
     - **Remote VPN Access Request**: Employee request &rarr; Department Head Approval &rarr; IT Provisioning & Sign-off Completion.
     - **Procurement & Purchase Request**: Requester draft &rarr; Technical/Budget specification &rarr; Reviewer sign-off &rarr; Executive decision.
     - **IT System Access Request**: Scope definition &rarr; Security audit &rarr; System owner approval.
     - **Expense Reimbursement**: Trip receipts claim &rarr; Finance audit &rarr; Director payment release.
   - **Full Audit Trail & Timeline**: Every action, field mutation, and comment is logged with timestamps and actors.
   - **Visual Stepper & Badges**: Clean progress bar and contextual action prompts.

2. **LDAP Authentication with Dev Fallback**:
   - Built on `ldap3` with support for secure binding, user DN templates, and LDAP search filters.
   - **Zero-Setup Dev Mode**: Includes `LDAP_MOCK_ENABLED=True` out-of-the-box so you can immediately sign in as:
     - `requester` (Alice Requester)
     - `reviewer` (Bob Technical Reviewer)
     - `approver` (Carol Executive Approver)
     - `admin` (Dave System Admin)
   - 1-click role switcher banner in development mode to test multi-person handoffs with zero friction.

3. **Mailing Notification Engine**:
   - Asynchronous dispatch via FastAPI background tasks or services.
   - Triggers notifications on:
     - Step assignment / action required
     - Workflow submitted for review
     - Final approval or rejection confirmation
   - **Interactive Developer Mailbox (`/dev/mailbox`)**: In development mode (`MAIL_ENABLED=False`), all emails are captured in an interactive UI with rendered HTML previews. Simply toggle `MAIL_ENABLED=True` and provide SMTP credentials to switch to real email delivery.

4. **Database Support (SQLite & MySQL)**:
   - Configurable via SQLAlchemy 2.0.
   - Default: `sqlite:///./workflow.db`.
   - Production MySQL: change `DATABASE_URL` in `.env` to `mysql+pymysql://user:password@host:port/dbname`. Connection pooling, ping checks, and disconnect recycles are built in.

5. **Extensible Architecture**:
   - Declarative `WorkflowDefinition` registry in `app/services/workflow_engine.py`. Adding a new workflow or modifying step transitions takes minutes without altering routes or database schemas.

---

## 📂 Project Structure

```
workflow-webapp/
├── app/
│   ├── config.py              # Pydantic Settings loaded from .env
│   ├── database.py            # SQLAlchemy engine setup (SQLite & MySQL)
│   ├── dependencies.py        # Auth & Role verification dependencies
│   ├── models/                # SQLAlchemy ORM models
│   │   ├── user.py            # User & LDAP profile model
│   │   └── workflow.py        # WorkflowInstance, StepHistory, and AuditLog
│   ├── schemas/               # Pydantic request/response schemas
│   │   ├── user.py
│   │   └── workflow.py
│   ├── workflows/             # Modular workflow definitions & registry
│   │   ├── base.py            # FormField, WorkflowStepDef, WorkflowDefinition
│   │   ├── registry.py        # WorkflowRegistry (register, get, list_all)
│   │   └── definitions/       # Individual workflow specification modules
│   │       ├── purchase_request.py
│   │       ├── it_access.py
│   │       ├── expense_reimbursement.py
│   │       └── remote_vpn.py
│   ├── services/              # Core business services
│   │   ├── auth_service.py    # LDAP authentication + session management
│   │   ├── workflow_engine.py # Core workflow execution & state transitions
│   │   └── mail_service.py    # SMTP & Mock Dev Mailbox notification service
│   ├── routers/               # Route handlers
│   │   ├── auth.py            # /login, /logout
│   │   ├── workflows.py       # Dashboard, create, multi-step edit, approve
│   │   ├── dev_tools.py       # /dev/mailbox, /dev/switch-user
│   │   └── api.py             # Headless REST API endpoints
│   ├── templates/             # Jinja2 modern HTML templates
│   │   ├── base.html          # Responsive layout, navbar, dev banner
│   │   ├── login.html         # LDAP login card + 1-click demo accounts
│   │   ├── dashboard.html     # Metrics, filters, and workflow tables
│   │   ├── workflows/
│   │   │   ├── create.html    # Step 1: Initial submission form
│   │   │   ├── detail.html    # Workflow overview, stepper, and audit history
│   │   │   ├── step_edit.html # Step 2/3: Multi-step interactive enrichment
│   │   │   └── approve.html   # Step 4: Executive sign-off & confirmation
│   │   └── dev/
│   │       └── mailbox.html   # Interactive email notification previewer
│   └── static/
│       ├── css/main.css       # Clean, modern, responsive CSS design system
│       └── js/main.js         # Client-side validation & toast helpers
├── main.py                    # FastAPI application entrypoint
├── pyproject.toml             # Modern PEP 621 package metadata & dependencies
├── requirements.txt           # Backward-compatible redirect to pyproject.toml (-e .)
├── requirements-dev.txt       # Development extras (-e .[dev])
├── .env.example               # Configuration reference template
└── .env                       # Active environment configuration
```

---

## 🚀 Quick Start Guide

### 1. Activate Environment & Install Dependencies

```bash
# On Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Install project & dependencies via pyproject.toml:
pip install -e .

# Or include testing & development extras:
pip install -e ".[dev]"
```

### 2. Run the Development Server

```bash
# Using registered console script:
workflow-server

# Or python directly:
python main.py

# Or uvicorn directly:
uvicorn main:app --reload --port 8000
```

Open your browser to:
**[http://localhost:8000](http://localhost:8000)**

---

## 🧪 Testing the Multi-Step Workflow

1. Navigate to `http://localhost:8000/login`.
2. Click **Requester (Alice)** to sign in.
3. Click **+ New Request** and submit a procurement request (e.g., *NVIDIA GPU Server Cluster*).
4. Notice the top banner has a 1-click **Role Switcher**:
   - Click **Reviewer (Bob)**: Open the workflow, click **Process Task**, enrich the request with vendor quote & GL code, and click **Save & Advance to Review**.
   - Continue as Reviewer to confirm readiness and submit for Executive Approval.
   - Click **Approver (Carol)**: Open the workflow, click **Sign-off / Approve**, provide formal executive notes, and click **Approve & Confirm Request**.
5. Visit **[http://localhost:8000/dev/mailbox](http://localhost:8000/dev/mailbox)** to inspect all automated email notifications that were triggered during the handoffs!

---

## 🔧 Production Configuration

### Switching from SQLite to MySQL

1. In MySQL, create a database and user:
   ```sql
   CREATE DATABASE workflow_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'workflow_user'@'%' IDENTIFIED BY 'YourStrongPassword123!';
   GRANT ALL PRIVILEGES ON workflow_db.* TO 'workflow_user'@'%';
   FLUSH PRIVILEGES;
   ```
2. In your `.env` file, change `DATABASE_URL`:
   ```ini
   DATABASE_URL=mysql+pymysql://workflow_user:YourStrongPassword123!@localhost:3306/workflow_db
   ```
3. Restart the app. SQLAlchemy will automatically connect to MySQL and provision tables.

### Connecting to Real LDAP / Active Directory

In `.env`, set:
```ini
LDAP_MOCK_ENABLED=False
LDAP_SERVER=ldap://ldap.yourcompany.com:389
LDAP_BASE_DN=dc=company,dc=com
LDAP_USER_DN_TEMPLATE=uid={username},ou=users,dc=company,dc=com
LDAP_SEARCH_FILTER=(sAMAccountName={username})
LDAP_BIND_USER_DN=cn=service_account,ou=services,dc=company,dc=com
LDAP_BIND_USER_PASSWORD=YourServicePassword
LDAP_USE_SSL=False
```

### Enabling Real SMTP Notifications

In `.env`, set:
```ini
MAIL_ENABLED=True
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USERNAME=notifications@yourcompany.com
MAIL_PASSWORD=YourAppPassword
MAIL_FROM=notifications@yourcompany.com
MAIL_STARTTLS=True
```

---

## 🧩 Adding New Workflows or Custom Steps

The workflow engine is completely generic. You can register any new workflow request type without modifying route handlers, database schemas, or HTML templates.

To register a new workflow, call `WorkflowRegistry.register(...)` in [app/services/workflow_engine.py](file:///e:/workflow-webapp/app/services/workflow_engine.py):

```python
from app.services.workflow_engine import WorkflowRegistry, WorkflowDefinition, WorkflowStepDef, FormField

CUSTOM_APPROVAL_WORKFLOW = WorkflowDefinition(
    type_id="vendor_onboarding",
    title="Vendor Onboarding Request",
    description="Multi-step vendor vetting and risk assessment.",
    icon_badge="Compliance",
    initial_step="step_1_vendor_info",
    step_order=["step_1_vendor_info", "step_2_risk_audit", "step_3_approval", "completed"],
    steps={
        "step_1_vendor_info": WorkflowStepDef(
            step_id="step_1_vendor_info",
            title="Step 1: Vendor Details",
            description="Submit basic corporate details and tax documents.",
            assigned_role="requester",
            fields=[
                FormField(name="company_name", label="Legal Entity Name", type="text", required=True),
                FormField(name="tax_id", label="Federal Tax ID / EIN", type="text", required=True),
                FormField(name="business_justification", label="Engagement Scope", type="textarea", required=True)
            ],
            transitions={
                "SUBMIT": ("step_2_risk_audit", "PENDING_REVIEW", "reviewer"),
                "SAVE_DRAFT": ("step_1_vendor_info", "DRAFT", "requester")
            }
        ),
        "step_2_risk_audit": WorkflowStepDef(
            step_id="step_2_risk_audit",
            title="Step 2: Legal & Risk Review",
            description="Risk officer reviews insurance and compliance.",
            assigned_role="reviewer",
            fields=[
                FormField(name="insurance_verified", label="COI Liability Coverage", type="select", options=["Compliant", "Exception Granted", "Non-Compliant"], required=True),
                FormField(name="risk_notes", label="Compliance Assessment Notes", type="textarea", required=True)
            ],
            transitions={
                "UPDATE_STEP": ("step_3_approval", "PENDING_APPROVAL", "approver"),
                "REQUEST_REVISION": ("step_1_vendor_info", "REVISION_REQUESTED", "requester")
            }
        ),
        "step_3_approval": WorkflowStepDef(
            step_id="step_3_approval",
            title="Step 3: Executive Confirmation",
            description="Procurement director formal signoff.",
            assigned_role="approver",
            is_approval_step=True,
            fields=[],
            transitions={
                "APPROVE": ("completed", "APPROVED", "requester"),
                "REJECT": ("rejected", "REJECTED", "requester"),
                "REQUEST_CHANGES": ("step_1_vendor_info", "REVISION_REQUESTED", "requester")
            }
        ),
        "completed": WorkflowStepDef(step_id="completed", title="Onboarding Approved", description="Vendor approved.", assigned_role="admin", is_interactive_form=False),
        "rejected": WorkflowStepDef(step_id="rejected", title="Onboarding Rejected", description="Vendor rejected.", assigned_role="admin", is_interactive_form=False)
    }
)

WorkflowRegistry.register(CUSTOM_APPROVAL_WORKFLOW)
```

The system automatically:
1. Renders the template in the **New Request** selector with its badge.
2. Generates dynamic form inputs and validation rules for each step.
3. Calculates progress stages on the visual stepper dynamically.
4. Handles role permissions and handoff transitions.
5. Assembles all accumulated dossier data on the executive review & approval page.

---

## 🔐 Encrypting Passwords & Secrets in `.env`

To prevent sensitive credentials (e.g. `LDAP_BIND_USER_PASSWORD`, `DATABASE_URL`, `MAIL_PASSWORD`) from being stored in plaintext in `.env`, the portal includes built-in AES-128/Fernet authenticated encryption.

### 1. Generate an Encryption Key
Run the built-in CLI:
```bash
python -m app.utils.crypto generate-key --save
```
This generates a 32-byte Fernet key and writes it to `.secret.key` (which is excluded by `.gitignore`).

Alternatively, set the key as an OS environment variable on your server or container:
```bash
# Windows PowerShell
$env:APP_ENCRYPTION_KEY="<your-generated-key>"

# Linux / Docker
export APP_ENCRYPTION_KEY="<your-generated-key>"
```

### 2. Encrypt Your Passwords
Use the CLI to encrypt any secret string:
```bash
# Encrypt LDAP password:
python -m app.utils.crypto encrypt "MyCorporateLdapBindPass!2026"

# Encrypt a production database connection string:
python -m app.utils.crypto encrypt "mysql+pymysql://workflow_svc:ProdP@ssw0rd@10.0.1.50:3306/workflow_prod"
```

The output will be formatted as `ENC(...)`:
```
[Encrypted Value for .env]:
ENC(gAAAAABq2BadITBGn74T5vIFZrqcp63OXu54Cps6f5t1VfNWThHNRsDzxws4GPXgk3812l-E03lArtyu90cCXQ0fqrsPG1Qj-GRZoDlnTItbaHmbeADcLXU=)
```

### 3. Place into `.env`
Paste the `ENC(...)` tokens directly into your `.env`:
```ini
# Real LDAP with encrypted bind password
LDAP_MOCK_ENABLED=False
LDAP_SERVER=ldaps://ldap.corp.internal:636
LDAP_BIND_USER_DN=cn=workflow-svc,ou=services,dc=corp,dc=internal
LDAP_BIND_USER_PASSWORD="ENC(gAAAAABq2BadITBGn74T5vIFZrqcp63OXu54Cps6f5t1VfNWThHNRsDzxws4GPXgk3812l-E03lArtyu90cCXQ0fqrsPG1Qj-GRZoDlnTItbaHmbeADcLXU=)"

# Database with encrypted connection string
DATABASE_URL="ENC(gAAAAABq2BadITBGn74...)"
```

### 4. Zero Code Changes Needed
At application startup, [Settings](file:///e:/workflow-webapp/app/config.py) automatically identifies any values wrapped in `ENC(...)` and decrypts them in-memory using `APP_ENCRYPTION_KEY` or `.secret.key`. 
- Plaintext values continue to work without modification (100% backward compatible for local dev).
- Passwords are never committed or exposed in logs.
