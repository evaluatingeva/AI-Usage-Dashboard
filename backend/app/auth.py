"""Authentication + role-based access control.

Roles (map these to Entra ID app roles / AD groups in production):
  admin         full access incl. ingest keys, price catalog, tool inventory
  finance       org-wide analytics, budgets, licenses, chargeback export
  auditor       org-wide read-only analytics + audit log (internal audit / risk)
  dept_manager  analytics limited to their own department
  viewer        their own usage only (employee self-service)
"""
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import IngestKey, UsageEvent, User

settings = get_settings()
bearer = HTTPBearer(auto_error=False)

ROLES = ("admin", "finance", "auditor", "dept_manager", "viewer")
ORG_WIDE_ROLES = {"admin", "finance", "auditor"}

PERMISSIONS: dict[str, set[str]] = {
    "analytics:read": set(ROLES),
    "licenses:read": {"admin", "finance", "auditor", "dept_manager"},
    "licenses:write": {"admin", "finance"},
    "budgets:read": {"admin", "finance", "auditor", "dept_manager"},
    "budgets:write": {"admin", "finance"},
    "audit:read": {"admin", "auditor"},
    "admin:write": {"admin"},
    "export:read": {"admin", "finance", "auditor"},
}


@dataclass
class Principal:
    id: str
    name: str
    role: str
    department_id: str | None = None
    team_id: str | None = None

    def can(self, perm: str) -> bool:
        return self.role in PERMISSIONS.get(perm, set())

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role,
                "department_id": self.department_id, "team_id": self.team_id}


# --------------------------------------------------------------------- tokens
def issue_dev_token(user: User) -> str:
    if settings.auth_mode != "dev":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "dev login disabled")
    now = datetime.now(UTC)
    payload = {
        "sub": user.id, "name": user.display_name, "role": user.role,
        "department_id": user.department_id, "team_id": user.team_id,
        "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes), "iss": "aiusage-dev",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


_jwks_client: jwt.PyJWKClient | None = None


def _decode_oidc(token: str) -> dict:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True)
    key = _jwks_client.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256"], audience=settings.oidc_audience,
                      issuer=settings.oidc_issuer)


def _principal_from_claims(claims: dict, db: Session) -> Principal:
    if settings.auth_mode == "dev":
        return Principal(claims["sub"], claims.get("name", claims["sub"]), claims.get("role", "viewer"),
                         claims.get("department_id"), claims.get("team_id"))
    uid = (claims.get("preferred_username") or claims.get("upn") or claims.get("email") or claims["sub"]).lower()
    roles = claims.get(settings.oidc_role_claim) or []
    # Highest-privilege role wins, unknown roles fall back to viewer
    role = next((r for r in ROLES if r in roles), "viewer")
    user = db.get(User, uid)
    return Principal(uid, claims.get("name", uid), role,
                     claims.get(settings.oidc_department_claim) or (user.department_id if user else None),
                     user.team_id if user else None)


def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        if settings.auth_mode == "dev":
            claims = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"], issuer="aiusage-dev")
        else:
            claims = _decode_oidc(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    return _principal_from_claims(claims, db)


def require(perm: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{p.role}' lacks '{perm}'")
        return p
    return dep


def usage_scope(p: Principal) -> list:
    """Row-level security for analytics: SQLAlchemy filters for the caller's scope."""
    if p.role in ORG_WIDE_ROLES:
        return []
    if p.role == "dept_manager":
        return [UsageEvent.department_id == p.department_id]
    return [UsageEvent.user_id == p.id]


# ---------------------------------------------------------------- ingest keys
def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def ingest_key_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> IngestKey:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key")
    key = db.scalar(select(IngestKey).where(IngestKey.key_hash == hash_key(x_api_key), IngestKey.revoked.is_(False)))
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked ingest key")
    return key
