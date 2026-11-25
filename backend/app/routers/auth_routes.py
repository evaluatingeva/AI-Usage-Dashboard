from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, current_principal, issue_dev_token
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..schemas import LoginIn, TokenOut
from ..services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.get("/config")
def auth_config():
    """Tells the frontend which login flow to use."""
    return {"mode": settings.auth_mode, "issuer": settings.oidc_issuer, "audience": settings.oidc_audience}


@router.get("/dev-users")
def dev_users(db: Session = Depends(get_db)):
    if settings.auth_mode != "dev":
        raise HTTPException(404)
    users = db.scalars(select(User).where(User.role != "viewer").order_by(User.role)).all()
    viewers = db.scalars(select(User).where(User.role == "viewer").limit(3)).all()
    return [{"id": u.id, "name": u.display_name, "role": u.role, "department_id": u.department_id}
            for u in [*users, *viewers]]


@router.post("/dev-login", response_model=TokenOut)
def dev_login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.get(User, body.user_id)
    if not user or not user.active:
        raise HTTPException(401, "unknown user")
    token = issue_dev_token(user)
    audit(db, user.id, "auth.login", "dev-login", ip=request.client.host if request.client else None)
    return TokenOut(access_token=token, user={"id": user.id, "name": user.display_name, "role": user.role,
                                             "department_id": user.department_id})


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    return p.as_dict()
