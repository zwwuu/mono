from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.requests import Request

from apps.workflow_app.database import get_db
from apps.workflow_app.models.user import User
from apps.workflow_app.services.auth_service import AuthService


class RedirectException(Exception):
    def __init__(self, url: str):
        self.url = url


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Retrieves authenticated user from session, or None."""
    return AuthService.get_session_user(request, db)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Ensures user is logged in. Redirects browser requests to /login."""
    user = AuthService.get_session_user(request, db)
    if not user:
        accept = request.headers.get("accept", "")
        # If web browser navigation, redirect to login
        if "text/html" in accept or request.url.path.startswith("/dashboard") or request.url.path.startswith("/workflows"):
            raise RedirectException(url=f"/login?next={request.url.path}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_roles(allowed_roles: List[str]):
    """Returns a dependency verifying that the user holds one of the specified roles or admin."""
    def role_checker(user: User = Depends(require_user)) -> User:
        if user.role == "admin" or user.role in allowed_roles:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. Requires one of roles: {', '.join(allowed_roles)}"
        )
    return role_checker
