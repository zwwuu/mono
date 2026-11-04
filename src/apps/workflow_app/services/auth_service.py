import logging
from typing import Dict, Optional

from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException
from sqlalchemy.orm import Session
from starlette.requests import Request

from apps.workflow_app.config import settings
from apps.workflow_app.models.user import User

logger = logging.getLogger("workflow.auth")

# Predefined mock users for out-of-the-box local testing and demonstrations
DEMO_USERS = {
    "requester": {
        "username": "requester",
        "display_name": "Alice Requester",
        "email": "alice.requester@example.com",
        "role": "requester",
        "roles": ["requester", "employee"],
        "workflow_roles": {},
        "department": "Engineering"
    },
    "reviewer": {
        "username": "reviewer",
        "display_name": "Bob Technical Reviewer",
        "email": "bob.reviewer@example.com",
        "role": "reviewer",
        "roles": [
            "reviewer",
            "procurement_specialist",
            "budget_reviewer",
            "network_admin",
            "security_officer",
            "finance_auditor",
            "finance_controller"
        ],
        "workflow_roles": {
            "purchase_request": ["procurement_specialist", "budget_reviewer"],
            "remote_vpn_request": ["network_admin"],
            "it_access_request": ["security_officer"],
            "expense_reimbursement": ["finance_auditor"],
            "capital_project": ["finance_controller"]
        },
        "department": "Finance & Architecture"
    },
    "approver": {
        "username": "approver",
        "display_name": "Carol Executive Approver",
        "email": "carol.approver@example.com",
        "role": "approver",
        "roles": [
            "approver",
            "dept_head",
            "system_owner",
            "finance_director",
            "executive_approver",
            "capex_lead"
        ],
        "workflow_roles": {
            "remote_vpn_request": ["dept_head"],
            "it_access_request": ["system_owner"],
            "expense_reimbursement": ["finance_director"],
            "purchase_request": ["executive_approver"],
            "capital_project": ["capex_lead"]
        },
        "department": "Executive Leadership"
    },
    "admin": {
        "username": "admin",
        "display_name": "Dave System Admin",
        "email": "admin@example.com",
        "role": "admin",
        "roles": ["admin", "network_admin", "it_helpdesk"],
        "workflow_roles": {
            "remote_vpn_request": ["network_admin"]
        },
        "department": "IT Operations"
    }
}


def seed_initial_users(db: Session):
    """Ensure demo users exist in the database with their feature roles."""
    for uname, info in DEMO_USERS.items():
        existing = db.query(User).filter(User.username == uname).first()
        if not existing:
            user = User(
                username=info["username"],
                display_name=info["display_name"],
                email=info["email"],
                role=info["role"],
                department=info["department"]
            )
            user.roles = info.get("roles", [info["role"]])
            user.workflow_roles = info.get("workflow_roles", {})
            db.add(user)
        else:
            # Sync roles if existing record has empty roles
            if not existing.roles or existing.roles == [existing.role]:
                existing.roles = info.get("roles", [existing.role])
            if not existing.workflow_roles:
                existing.workflow_roles = info.get("workflow_roles", {})
    db.commit()


class AuthService:
    @classmethod
    def authenticate_ldap(cls, username: str, password: str) -> Optional[Dict]:
        """
        Attempts to authenticate the user against the configured LDAP server.
        Returns user info dictionary if successful, or None on failure.
        """
        if not password:
            return None

        # 1. Dev / Demo Fallback Check
        if settings.LDAP_MOCK_ENABLED and username in DEMO_USERS:
            # For demo ease, accepts any password or 'password'
            logger.info(f"[AUTH] Authenticated demo user '{username}' via mock credentials.")
            return DEMO_USERS[username]

        # 2. Real LDAP Connection
        try:
            server = Server(settings.LDAP_SERVER, use_ssl=settings.LDAP_USE_SSL, get_info=ALL)
            
            # If service account bind is configured, perform search
            if settings.LDAP_BIND_USER_DN and settings.LDAP_BIND_USER_PASSWORD:
                search_conn = Connection(
                    server,
                    user=settings.LDAP_BIND_USER_DN,
                    password=settings.LDAP_BIND_USER_PASSWORD,
                    auto_bind=True
                )
                search_filter = settings.LDAP_SEARCH_FILTER.format(username=username)
                search_conn.search(
                    search_base=settings.LDAP_BASE_DN,
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=["mail", "displayName", "cn", "department", "memberOf"]
                )
                if not search_conn.entries:
                    search_conn.unbind()
                    logger.warning(f"[LDAP] User not found: {username}")
                    return None

                user_entry = search_conn.entries[0]
                user_dn = user_entry.entry_dn
                search_conn.unbind()
            else:
                user_dn = settings.LDAP_USER_DN_TEMPLATE.format(username=username)
                user_entry = None

            # Attempt direct bind with user credentials
            user_conn = Connection(server, user=user_dn, password=password)
            if not user_conn.bind():
                logger.warning(f"[LDAP] Bind failed for user {username}")
                return None

            user_conn.unbind()

            # Extract LDAP attributes
            display_name = username
            email = f"{username}@example.com"
            department = "General"
            role = "requester"

            if user_entry:
                if hasattr(user_entry, "displayName") and user_entry.displayName.value:
                    display_name = str(user_entry.displayName.value)
                elif hasattr(user_entry, "cn") and user_entry.cn.value:
                    display_name = str(user_entry.cn.value)
                
                if hasattr(user_entry, "mail") and user_entry.mail.value:
                    email = str(user_entry.mail.value)
                    
                if hasattr(user_entry, "department") and user_entry.department.value:
                    department = str(user_entry.department.value)

            return {
                "username": username,
                "display_name": display_name,
                "email": email,
                "role": role,
                "department": department
            }
        except LDAPException as e:
            logger.error(f"[LDAP Error] {e}")
            # If in mock-enabled mode and LDAP failed, try demo user fallback
            if settings.LDAP_MOCK_ENABLED and username in DEMO_USERS:
                return DEMO_USERS[username]
            return None
        except Exception as e:
            logger.error(f"[Auth Error] {e}")
            if settings.LDAP_MOCK_ENABLED and username in DEMO_USERS:
                return DEMO_USERS[username]
            return None

    @classmethod
    def get_or_create_user(cls, db: Session, user_info: Dict) -> User:
        """Syncs LDAP authenticated user with local DB, always refreshing feature roles."""
        user = db.query(User).filter(User.username == user_info["username"]).first()
        if not user:
            user = User(
                username=user_info["username"],
                display_name=user_info.get("display_name", user_info["username"]),
                email=user_info.get("email", f"{user_info['username']}@example.com"),
                role=user_info.get("role", "requester"),
                department=user_info.get("department", "General")
            )
            db.add(user)
        else:
            # Always sync mutable profile fields
            if user_info.get("display_name"):
                user.display_name = user_info["display_name"]
            if user_info.get("email"):
                user.email = user_info["email"]
            if user_info.get("role"):
                user.role = user_info["role"]
            if user_info.get("department"):
                user.department = user_info["department"]

        # Always sync feature roles (demo or LDAP-sourced)
        if user_info.get("roles"):
            user.roles = user_info["roles"]
        if user_info.get("workflow_roles"):
            user.workflow_roles = user_info["workflow_roles"]

        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def set_session_user(request: Request, user: User):
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["display_name"] = user.display_name
        request.session["role"] = user.role

    @staticmethod
    def clear_session_user(request: Request):
        request.session.clear()

    @staticmethod
    def get_session_user(request: Request, db: Session) -> Optional[User]:
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        return db.query(User).filter(User.id == user_id, User.is_active == True).first()
