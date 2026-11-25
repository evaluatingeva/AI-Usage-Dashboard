import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import ROLES, Principal, hash_key, require
from ..config import get_settings
from ..db import get_db
from ..directory.apply import apply_directory, reattribute
from ..directory.csv_import import parse_csv
from ..models import AITool, AuditLog, Budget, CollectorRun, Department, IngestKey, License, PriceCatalog, User
from ..schemas import BudgetIn, IngestKeyIn, LicenseIn, PriceIn, ToolIn, UserUpdate
from ..services.audit import audit, verify_chain

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _ip(r: Request) -> str | None:
    return r.client.host if r.client else None


# ---------------------------------------------------------------- AI inventory
@router.get("/tools")
def list_tools(p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    return db.scalars(select(AITool).order_by(AITool.name)).all()


@router.put("/tools/{tool_id}")
def upsert_tool(tool_id: str, body: ToolIn, request: Request, p: Principal = Depends(require("admin:write")),
                db: Session = Depends(get_db)):
    db.merge(AITool(**{**body.model_dump(), "id": tool_id}))
    audit(db, p.id, "tool.upsert", tool_id, body.model_dump(), _ip(request))
    return {"ok": True}


# --------------------------------------------------------------- price catalog
@router.get("/prices")
def list_prices(p: Principal = Depends(require("licenses:read")), db: Session = Depends(get_db)):
    return db.scalars(select(PriceCatalog).order_by(PriceCatalog.provider, PriceCatalog.model)).all()


@router.post("/prices")
def add_price(body: PriceIn, request: Request, p: Principal = Depends(require("admin:write")),
              db: Session = Depends(get_db)):
    row = PriceCatalog(**body.model_dump())
    db.add(row)
    db.flush()
    audit(db, p.id, "price.create", f"{body.provider}/{body.model}/{body.unit_type}", body.model_dump(), _ip(request))
    return {"id": row.id}


# -------------------------------------------------------------------- licenses
@router.post("/licenses")
def add_license(body: LicenseIn, request: Request, p: Principal = Depends(require("licenses:write")),
                db: Session = Depends(get_db)):
    row = License(**body.model_dump())
    db.add(row)
    db.flush()
    audit(db, p.id, "license.create", body.tool_id, body.model_dump(), _ip(request))
    return {"id": row.id}


# --------------------------------------------------------------------- budgets
@router.post("/budgets")
def add_budget(body: BudgetIn, request: Request, p: Principal = Depends(require("budgets:write")),
               db: Session = Depends(get_db)):
    row = Budget(**body.model_dump())
    db.add(row)
    db.flush()
    audit(db, p.id, "budget.create", f"{body.scope_type}:{body.scope_id}", body.model_dump(), _ip(request))
    return {"id": row.id}


@router.delete("/budgets/{budget_id}")
def delete_budget(budget_id: int, request: Request, p: Principal = Depends(require("budgets:write")),
                  db: Session = Depends(get_db)):
    row = db.get(Budget, budget_id)
    if not row:
        raise HTTPException(404)
    db.delete(row)
    audit(db, p.id, "budget.delete", str(budget_id), None, _ip(request))
    return {"ok": True}


# ----------------------------------------------------------------- ingest keys
@router.get("/ingest-keys")
def list_keys(p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    return [{"id": k.id, "name": k.name, "prefix": k.key_prefix, "allowed_sources": k.allowed_sources,
             "created_by": k.created_by, "created_at": k.created_at, "revoked": k.revoked}
            for k in db.scalars(select(IngestKey).order_by(IngestKey.id))]


@router.post("/ingest-keys")
def create_key(body: IngestKeyIn, request: Request, p: Principal = Depends(require("admin:write")),
               db: Session = Depends(get_db)):
    raw = "aiu_" + secrets.token_urlsafe(32)
    db.add(IngestKey(name=body.name, key_prefix=raw[:12], key_hash=hash_key(raw),
                     allowed_sources=body.allowed_sources, created_by=p.id))
    audit(db, p.id, "ingest_key.create", body.name, {"prefix": raw[:12]}, _ip(request))
    return {"api_key": raw, "note": "Shown once. Store it in your secret manager."}


@router.post("/ingest-keys/{key_id}/revoke")
def revoke_key(key_id: int, request: Request, p: Principal = Depends(require("admin:write")),
               db: Session = Depends(get_db)):
    k = db.get(IngestKey, key_id)
    if not k:
        raise HTTPException(404)
    k.revoked = True
    audit(db, p.id, "ingest_key.revoke", k.name, None, _ip(request))
    return {"ok": True}


# ----------------------------------------------------------------------- audit
@router.get("/audit")
def audit_log(limit: int = 200, p: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))).all()
    return [{"id": r.id, "ts": r.ts, "actor": r.actor, "action": r.action, "resource": r.resource,
             "ip": r.ip, "details": r.details, "hash": r.row_hash[:12]} for r in rows]


@router.get("/audit/verify")
def audit_verify(p: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    return verify_chain(db)


# ------------------------------------------------------------------ collectors
@router.get("/collectors")
def collectors_status(p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    from ..collectors.registry import enabled_collectors

    runs = db.scalars(select(CollectorRun).order_by(CollectorRun.id.desc()).limit(50)).all()
    latest: dict[str, CollectorRun] = {}
    for r in runs:
        latest.setdefault(r.collector, r)
    return {
        "enabled": [c.name for c in enabled_collectors()],
        "kinds": {c.name: c.kind for c in enabled_collectors()},
        "latest": [{"collector": n, "status": r.status, "events": r.events, "error": r.error,
                    "started_at": r.started_at, "finished_at": r.finished_at} for n, r in latest.items()],
        "recent_runs": runs,
    }


@router.post("/collectors/{name}/run")
def run_collector(name: str, request: Request, background: BackgroundTasks,
                  p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    from ..collectors.registry import enabled_collectors, run_one_quiet

    match = [c for c in enabled_collectors() if c.name == name]
    if not match:
        raise HTTPException(404, f"collector '{name}' is not enabled")
    background.add_task(run_one_quiet, match[0])
    audit(db, p.id, "collector.run", name, None, _ip(request))
    return {"started": name}


# ------------------------------------------------------------ people directory
@router.get("/users")
def list_users(q: str | None = None, department_id: str | None = None, limit: int = 200,
               p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.display_name)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.display_name).like(like), func.lower(User.id).like(like)))
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    total = db.scalar(select(func.count()).select_from(User))
    no_dept = db.scalar(select(func.count()).select_from(User).where(User.department_id.is_(None)))
    return {"total": total, "without_department": no_dept,
            "departments": [{"id": d.id, "name": d.name} for d in db.scalars(select(Department).order_by(Department.name))],
            "users": [{"id": u.id, "display_name": u.display_name, "department_id": u.department_id,
                       "team_id": u.team_id, "role": u.role, "aliases": u.aliases or [], "active": u.active}
                      for u in db.scalars(stmt.limit(min(limit, 1000)))]}


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, request: Request, p: Principal = Depends(require("admin:write")),
                db: Session = Depends(get_db)):
    """Grant a role (e.g. make someone `dept_manager` of their department) or fix attribution."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    changes = body.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] not in ROLES:
        raise HTTPException(422, f"role must be one of {sorted(ROLES)}")
    if changes.get("department_id") and db.get(Department, changes["department_id"]) is None:
        raise HTTPException(422, "unknown department")
    if changes.get("role") == "dept_manager" and not (changes.get("department_id") or u.department_id):
        raise HTTPException(422, "a dept_manager needs a department")
    for k, v in changes.items():
        setattr(u, k, v)
    db.flush()
    n = reattribute(db, get_settings().directory_reattribute_days) if "department_id" in changes else 0
    audit(db, p.id, "user.update", user_id, changes, _ip(request))
    return {"ok": True, "events_reattributed": n}


@router.post("/directory/import")
async def import_directory(file: UploadFile, request: Request, deactivate_missing: bool = False,
                           restate: bool = False, p: Principal = Depends(require("admin:write")),
                           db: Session = Depends(get_db)):
    """Upload a people CSV from any HR system (see app/directory/csv_import.py for headers)."""
    try:
        people = parse_csv((await file.read()).decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    st = apply_directory(db, people, "csv", deactivate_missing=deactivate_missing,
                         reattribute_days=get_settings().directory_reattribute_days, restate=restate)
    result = {k: v for k, v in st.__dict__.items() if k != "skipped"} | {"skipped": len(st.skipped)}
    audit(db, p.id, "directory.import", file.filename, result, _ip(request))
    return result


@router.post("/directory/reattribute")
def reattribute_now(request: Request, days: int = 90, restate: bool = False,
                    p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """Back-fill (or with restate=true, overwrite) department/team on events of the last N days."""
    n = reattribute(db, min(days, 800), restate)
    db.commit()
    audit(db, p.id, "directory.reattribute", None, {"days": days, "restate": restate, "events": n}, _ip(request))
    return {"events_reattributed": n}
