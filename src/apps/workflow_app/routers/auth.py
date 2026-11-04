from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from apps.workflow_app.config import settings
from apps.workflow_app.database import get_db
from apps.workflow_app.paths import app_dir
from apps.workflow_app.services.auth_service import AuthService

router = APIRouter()
templates = Jinja2Templates(directory=str(app_dir() / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = None, db: Session = Depends(get_db)):
    user = AuthService.get_session_user(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "settings": settings,
            "next_url": next,
            "current_user": None,
            "error": None
        }
    )


@router.post("/login", response_class=HTMLResponse)
async def process_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user_info = AuthService.authenticate_ldap(username=username.strip(), password=password)
    if not user_info:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "settings": settings,
                "next_url": next_url,
                "current_user": None,
                "error": "Invalid LDAP credentials or user not authorized."
            },
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    # Sync or create user record in DB
    user = AuthService.get_or_create_user(db, user_info)
    AuthService.set_session_user(request, user)

    redirect_target = next_url if next_url and next_url.startswith("/") else "/dashboard"
    return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)


@router.get("/logout")
async def logout(request: Request):
    AuthService.clear_session_user(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
