from typing import Optional
from fastapi import APIRouter, Depends, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.user import User
from app.services.auth_service import AuthService, DEMO_USERS
from app.services.mail_service import DEV_MAILBOX
from app.dependencies import require_user, get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dev/mailbox", response_class=HTMLResponse)
async def view_mailbox(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user)
):
    return templates.TemplateResponse(
        request=request,
        name="dev/mailbox.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "active_nav": "mailbox",
            "emails": DEV_MAILBOX
        }
    )


@router.post("/dev/mailbox/clear")
async def clear_mailbox():
    DEV_MAILBOX.clear()
    return RedirectResponse(url="/dev/mailbox", status_code=status.HTTP_302_FOUND)


@router.get("/dev/switch-user")
async def dev_switch_user(
    request: Request,
    user: str,
    next: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Dev shortcut to quickly swap between demo roles."""
    if not settings.LDAP_MOCK_ENABLED:
        raise HTTPException(status_code=403, detail="Quick switch is only available when LDAP_MOCK_ENABLED is True.")

    target_user_info = DEMO_USERS.get(user.lower())
    if not target_user_info:
        raise HTTPException(status_code=400, detail=f"Unknown demo user '{user}'.")

    db_user = AuthService.get_or_create_user(db, target_user_info)
    AuthService.set_session_user(request, db_user)

    redirect_url = next if next and next.startswith("/") else "/dashboard"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
