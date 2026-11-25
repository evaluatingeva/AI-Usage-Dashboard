"""Read-side API that powers the dashboard. Every query is passed through
`usage_scope()` so dept managers and employees only ever see their own slice."""
import csv
import io
import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Date, Integer, cast, distinct, func, select
from sqlalchemy.orm import Session

from ..auth import ORG_WIDE_ROLES, Principal, require, usage_scope
from ..config import get_settings
from ..db import engine, get_db
from ..models import AITool, Budget, Department, License, SeatAssignment, Team, UsageEvent, User
from ..services.audit import audit

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
settings = get_settings()
logger = logging.getLogger("api.analytics")

Dimension = Literal["provider", "tool_id", "model", "event_type", "department_id", "team_id",
                    "user_id", "project", "application", "unit_type", "vector_store"]
Metric = Literal["cost", "tokens", "input_tokens", "output_tokens", "requests", "quantity", "documents"]

E = UsageEvent


def day_expr():
    return func.date(E.ts) if engine.dialect.name == "sqlite" else cast(E.ts, Date)


def metric_expr(m: str):
    return {
        "cost": func.sum(E.cost_usd),
        "tokens": func.sum(E.input_tokens + E.output_tokens),
        "input_tokens": func.sum(E.input_tokens),
        "output_tokens": func.sum(E.output_tokens),
        "requests": func.sum(E.requests),
        "quantity": func.sum(E.quantity),
        "documents": func.sum(E.documents_retrieved),
    }[m]


class Filters:
    def __init__(self, start: date | None = None, end: date | None = None, provider: str | None = None,
                 tool_id: str | None = None, department_id: str | None = None, model: str | None = None,
                 project: str | None = None, user_id: str | None = None, team_id: str | None = None,
                 application: str | None = None):
        self.end = end or datetime.now(UTC).date()
        self.start = start or self.end - timedelta(days=29)
        if self.start > self.end:
            raise HTTPException(422, "start must be <= end")
        self.provider, self.tool_id, self.department_id = provider, tool_id, department_id
        self.model, self.project = model, project
        self.user_id, self.team_id, self.application = user_id, team_id, application

    def where(self, p: Principal, start: date | None = None, end: date | None = None) -> list:
        s, e = start or self.start, end or self.end
        conds = [E.ts >= datetime.combine(s, datetime.min.time(), UTC),
                 E.ts < datetime.combine(e + timedelta(days=1), datetime.min.time(), UTC),
                 *usage_scope(p)]
        for col, val in ((E.provider, self.provider), (E.tool_id, self.tool_id),
                         (E.department_id, self.department_id), (E.model, self.model), (E.project, self.project),
                         (E.user_id, self.user_id), (E.team_id, self.team_id), (E.application, self.application)):
            if val:
                conds.append(col == val)
        return conds


def _f(v) -> float:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _i(v) -> int:
    return int(_f(v))


def _safe(db: Session, what: str, fn, default):
    """Run an optional KPI query; on a database error log it, reset the transaction and fall back."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken KPI must not blank the whole Overview
        logger.exception("summary: %s query failed", what)
        db.rollback()
        return default


def _pct(cur: float, prev: float):
    if not prev:
        return None
    v = (cur - prev) / prev * 100
    return round(v, 1) if math.isfinite(v) else None


@router.get("/summary")
def summary(f: Filters = Depends(), p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    def totals(conds):
        r = db.execute(select(
            func.sum(E.cost_usd), func.sum(E.input_tokens), func.sum(E.output_tokens),
            func.sum(E.cached_input_tokens), func.sum(E.requests), func.count(distinct(E.user_id)),
        ).where(*conds)).one()
        rag = _safe(db, "rag", lambda: db.execute(
            select(func.sum(E.requests), func.sum(E.documents_retrieved))
            .where(*conds, E.event_type.in_(["rag_retrieval", "vector_query"]))).one(), (0, 0))
        emb = _safe(db, "embedding", lambda: db.scalar(
            select(func.sum(E.input_tokens)).where(*conds, E.event_type == "embedding")), 0)
        return {"cost_usd": _f(r[0]), "input_tokens": _i(r[1]), "output_tokens": _i(r[2]),
                "cached_input_tokens": _i(r[3]), "requests": _i(r[4]), "active_users": _i(r[5]),
                "rag_queries": _i(rag[0]), "rag_documents": _i(rag[1]), "embedding_tokens": _i(emb)}

    days = (f.end - f.start).days + 1
    cur = totals(f.where(p))
    prev = totals(f.where(p, f.start - timedelta(days=days), f.start - timedelta(days=1)))
    delta = {k: _pct(cur[k], prev[k]) for k in cur}
    seat_cost = 0.0
    if p.role in ORG_WIDE_ROLES:
        seat_cost = _safe(db, "licences", lambda: _f(db.scalar(
            select(func.sum(License.seats_purchased * License.price_per_seat_month_usd)))), 0.0)
    return {"period": {"start": f.start.isoformat(), "end": f.end.isoformat(), "days": days}, "current": cur,
            "previous": prev, "change_pct": delta, "seat_cost_monthly_usd": seat_cost}


@router.get("/timeseries")
def timeseries(metric: Metric = "cost", group_by: Dimension | None = None, f: Filters = Depends(),
               p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    d = day_expr().label("day")
    cols = [d, metric_expr(metric).label("value")]
    if group_by:
        cols.insert(1, getattr(E, group_by).label("key"))
    q = select(*cols).where(*f.where(p)).group_by(*[c for c in cols[:-1]]).order_by(d)
    out = []
    for r in db.execute(q):
        out.append({"date": str(r.day), "key": (r.key if group_by else "total") or "unattributed",
                    "value": _f(r.value)})
    return out


@router.get("/breakdown")
def breakdown(dimension: Dimension = "provider", metric: Metric = "cost", limit: int = 20,
              f: Filters = Depends(), p: Principal = Depends(require("analytics:read")),
              db: Session = Depends(get_db)):
    col = getattr(E, dimension)
    q = (select(col.label("key"), func.sum(E.cost_usd).label("cost"),
                func.sum(E.input_tokens).label("input_tokens"), func.sum(E.output_tokens).label("output_tokens"),
                func.sum(E.requests).label("requests"), func.count(distinct(E.user_id)).label("users"),
                metric_expr(metric).label("value"))
         .where(*f.where(p)).group_by(col).order_by(metric_expr(metric).desc()).limit(min(limit, 500)))
    labels = _labels(db, dimension)
    rows = []
    for r in db.execute(q):
        # k-anonymity: hide small groups from non-org-wide viewers
        if p.role not in ORG_WIDE_ROLES and dimension != "user_id" and r.users < settings.min_group_size:
            continue
        rows.append({"key": r.key or "unattributed", "label": labels.get(r.key, r.key or "Unattributed"),
                     "cost_usd": _f(r.cost), "input_tokens": int(r.input_tokens or 0),
                     "output_tokens": int(r.output_tokens or 0), "requests": int(r.requests or 0),
                     "users": int(r.users or 0), "value": _f(r.value)})
    return rows


def _labels(db: Session, dimension: str) -> dict:
    model = {"department_id": Department, "team_id": Team, "tool_id": AITool}.get(dimension)
    if model:
        return {r.id: r.name for r in db.scalars(select(model))}
    if dimension == "user_id":
        return {u.id: u.display_name for u in db.scalars(select(User))}
    return {}


@router.get("/rag")
def rag(f: Filters = Depends(), p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    conds = f.where(p)
    by_store = db.execute(
        select(E.vector_store, E.index_name, func.sum(E.requests).label("queries"),
               func.sum(E.documents_retrieved).label("docs"), func.avg(E.latency_ms).label("lat"),
               func.sum(E.cost_usd).label("cost"))
        .where(*conds, E.event_type.in_(["rag_retrieval", "vector_query", "rerank"]))
        .group_by(E.vector_store, E.index_name).order_by(func.sum(E.requests).desc())
    ).all()
    embeddings = db.execute(
        select(E.provider, E.model, func.sum(E.input_tokens).label("tokens"), func.sum(E.cost_usd).label("cost"))
        .where(*conds, E.event_type == "embedding").group_by(E.provider, E.model)
    ).all()
    d = day_expr().label("day")
    daily = db.execute(
        select(d, func.sum(E.requests).label("queries"), func.sum(E.documents_retrieved).label("docs"))
        .where(*conds, E.event_type.in_(["rag_retrieval", "vector_query"])).group_by(d).order_by(d)
    ).all()
    return {
        "stores": [{"vector_store": r.vector_store or "unknown", "index": r.index_name, "queries": int(r.queries or 0),
                    "documents": int(r.docs or 0), "avg_latency_ms": round(_f(r.lat), 1), "cost_usd": _f(r.cost)}
                   for r in by_store],
        "embeddings": [{"provider": r.provider, "model": r.model, "tokens": int(r.tokens or 0),
                        "cost_usd": _f(r.cost)} for r in embeddings],
        "daily": [{"date": str(r.day), "queries": int(r.queries or 0), "documents": int(r.docs or 0)} for r in daily],
    }


RAG_TYPES = ["rag_retrieval", "vector_query", "rerank", "embedding"]


@router.get("/rag/apps")
def rag_apps(f: Filters = Depends(), p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    """One row per RAG application: anything that sent retrieval, vector or embedding events.
    Totals include the app's LLM calls too, so this is the full cost of running the app."""
    conds = f.where(p)
    apps = [a for (a,) in db.execute(select(distinct(E.application))
                                     .where(*conds, E.event_type.in_(RAG_TYPES), E.application.is_not(None)))]
    if not apps:
        return []
    is_rag = E.event_type.in_(["rag_retrieval", "vector_query"])
    rows = db.execute(
        select(E.application,
               func.sum(func.coalesce(E.requests, 0) * is_rag.cast(Integer)).label("queries"),
               func.sum(func.coalesce(E.documents_retrieved, 0)).label("docs"),
               func.sum((E.event_type == "embedding").cast(Integer) * E.input_tokens).label("emb_tokens"),
               func.sum((E.event_type == "llm_call").cast(Integer) * (E.input_tokens + E.output_tokens)).label("llm_tokens"),
               func.sum((E.event_type == "llm_call").cast(Integer) * E.requests).label("llm_calls"),
               func.sum(E.cost_usd).label("cost"), func.count(distinct(E.user_id)).label("users"),
               func.max(E.ts).label("last_seen"), func.min(E.ts).label("first_seen"),
               func.sum((E.status == "error").cast(Integer) * E.requests).label("errors"),
               func.sum(E.requests).label("requests"))
        .where(*conds, E.application.in_(apps)).group_by(E.application)
    ).all()
    lat = dict(db.execute(select(E.application, func.avg(E.latency_ms))
                          .where(*conds, E.application.in_(apps), is_rag).group_by(E.application)).all())
    meta: dict[str, dict] = {a: {"stores": set(), "models": set(), "envs": set(), "depts": set()} for a in apps}
    for a, vs, m, env, d in db.execute(select(E.application, E.vector_store, E.model, E.environment, E.department_id)
                                       .where(*conds, E.application.in_(apps)).distinct()):
        mm = meta[a]
        vs and mm["stores"].add(vs)
        m and mm["models"].add(m)
        env and mm["envs"].add(env)
        d and mm["depts"].add(d)
    depts = _labels(db, "department_id")
    now = datetime.now(UTC)
    out = []
    for r in rows:
        last = r.last_seen if r.last_seen.tzinfo else r.last_seen.replace(tzinfo=UTC)
        age_min = (now - last).total_seconds() / 60
        status = "running" if age_min <= settings.app_live_minutes else "idle" if age_min <= 24 * 60 else "inactive"
        m = meta[r.application]
        req = _i(r.requests)
        out.append({
            "application": r.application, "status": status, "last_seen": last.isoformat(),
            "first_seen": (r.first_seen if r.first_seen.tzinfo else r.first_seen.replace(tzinfo=UTC)).isoformat(),
            "queries": _i(r.queries), "documents": _i(r.docs), "embedding_tokens": _i(r.emb_tokens),
            "llm_tokens": _i(r.llm_tokens), "llm_calls": _i(r.llm_calls), "cost_usd": _f(r.cost), "users": _i(r.users),
            "avg_retrieval_latency_ms": round(_f(lat.get(r.application)), 1),
            "error_rate_pct": round(_i(r.errors) / req * 100, 2) if req else 0.0,
            "vector_stores": sorted(m["stores"]), "models": sorted(m["models"]), "environments": sorted(m["envs"]),
            "departments": sorted(depts.get(d, d) for d in m["depts"]),
        })
    out.sort(key=lambda x: ({"running": 0, "idle": 1, "inactive": 2}[x["status"]], -x["cost_usd"]))
    return out


PROFILE_DIMS = {"user_id", "department_id", "team_id", "project", "application"}


@router.get("/profile")
def profile(dimension: str, key: str, f: Filters = Depends(), p: Principal = Depends(require("analytics:read")),
            db: Session = Depends(get_db)):
    """Drill-down for one user, department, team, engagement or application: what they use,
    which models, which apps, daily trend, and (for groups) the top users."""
    if dimension not in PROFILE_DIMS:
        raise HTTPException(422, f"dimension must be one of {sorted(PROFILE_DIMS)}")
    col = getattr(E, dimension)
    conds = [*f.where(p), col.is_(None) if key == "unattributed" else col == key]

    def group(c, limit=10):
        q = (select(c.label("k"), func.sum(E.cost_usd).label("cost"),
                    func.sum(E.input_tokens + E.output_tokens).label("tokens"), func.sum(E.requests).label("req"),
                    func.count(distinct(E.user_id)).label("users"))
             .where(*conds).group_by(c).order_by(func.sum(E.cost_usd).desc(), func.sum(E.requests).desc()).limit(limit))
        return db.execute(q).all()

    def rows(c, dim, limit=10):
        labels = _labels(db, dim)
        return [{"key": r.k or "unattributed", "label": labels.get(r.k, r.k or "Unattributed"), "cost_usd": _f(r.cost),
                 "tokens": _i(r.tokens), "requests": _i(r.req), "users": _i(r.users)} for r in group(c, limit)]

    t = db.execute(select(func.sum(E.cost_usd), func.sum(E.input_tokens), func.sum(E.output_tokens),
                          func.sum(E.requests), func.count(distinct(E.user_id)), func.max(E.ts),
                          func.sum(E.documents_retrieved)).where(*conds)).one()
    d = day_expr().label("day")
    daily = db.execute(select(d, func.sum(E.cost_usd).label("cost"), func.sum(E.input_tokens + E.output_tokens).label("tok"))
                       .where(*conds).group_by(d).order_by(d)).all()
    info: dict = {"label": _labels(db, dimension).get(key, key)}
    if dimension == "user_id":
        u = db.get(User, key)
        visible = u is not None and (p.role in ORG_WIDE_ROLES or p.id == key
                                     or (p.role == "dept_manager" and u.department_id == p.department_id))
        if visible:
            dep = db.get(Department, u.department_id) if u.department_id else None
            team = db.get(Team, u.team_id) if u.team_id else None
            info.update({"department": dep.name if dep else None, "department_id": u.department_id,
                         "team": team.name if team else None, "cost_center": dep.cost_center if dep else None,
                         "active": u.active})
        seats = db.execute(select(SeatAssignment.tool_id, SeatAssignment.last_active_at)
                           .where(SeatAssignment.user_id == key)).all() if visible else []
        info["seats"] = [{"tool_id": s.tool_id, "last_active_at": s.last_active_at} for s in seats]
    elif dimension == "department_id":
        dep = db.get(Department, key)
        if dep is not None:
            info.update({"cost_center": dep.cost_center,
                         "headcount": db.scalar(select(func.count()).select_from(User)
                                                .where(User.department_id == key, User.active.is_(True)))})
    out = {
        "dimension": dimension, "key": key, "info": info,
        "totals": {"cost_usd": _f(t[0]), "input_tokens": _i(t[1]), "output_tokens": _i(t[2]), "requests": _i(t[3]),
                   "active_users": _i(t[4]), "documents_retrieved": _i(t[6]),
                   "last_activity": t[5].isoformat() if t[5] else None},
        "by_tool": rows(E.tool_id, "tool_id"), "by_model": rows(E.model, "model"),
        "by_application": rows(E.application, "application"),
        "daily": [{"date": str(r.day), "cost_usd": _f(r.cost), "tokens": _i(r.tok)} for r in daily],
    }
    if dimension != "user_id":
        out["top_users"] = [] if (p.role not in ORG_WIDE_ROLES and p.role != "dept_manager") else rows(E.user_id, "user_id", 15)
        out["by_team"] = rows(E.team_id, "team_id") if dimension == "department_id" else []
    return out


@router.get("/non-token")
def non_token(f: Filters = Depends(), p: Principal = Depends(require("analytics:read")),
              db: Session = Depends(get_db)):
    """Usage measured in units other than tokens: images, audio minutes, API calls, searches..."""
    rows = db.execute(
        select(E.tool_id, E.provider, E.unit_type, func.sum(E.quantity).label("qty"),
               func.sum(E.requests).label("req"), func.sum(E.cost_usd).label("cost"))
        .where(*f.where(p), E.unit_type.is_not(None), E.event_type != "seat")
        .group_by(E.tool_id, E.provider, E.unit_type).order_by(func.sum(E.cost_usd).desc())
    ).all()
    tools = _labels(db, "tool_id")
    return [{"tool_id": r.tool_id, "tool": tools.get(r.tool_id, r.tool_id), "provider": r.provider,
             "unit_type": r.unit_type, "quantity": _f(r.qty), "requests": int(r.req or 0), "cost_usd": _f(r.cost)}
            for r in rows]


@router.get("/licenses")
def licenses(p: Principal = Depends(require("licenses:read")), db: Session = Depends(get_db)):
    """Seat-based tools: purchased vs assigned vs actually active (30 days)."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    out = []
    for lic, tool in db.execute(select(License, AITool).join(AITool, AITool.id == License.tool_id)):
        seat_q = select(SeatAssignment).where(SeatAssignment.tool_id == lic.tool_id)
        if p.role == "dept_manager":
            seat_q = seat_q.join(User, User.id == SeatAssignment.user_id).where(User.department_id == p.department_id)
        seats = db.scalars(seat_q).all()
        active = sum(1 for s in seats if s.last_active_at and _aware(s.last_active_at) >= cutoff)
        price = _f(lic.price_per_seat_month_usd)
        purchased = lic.seats_purchased
        out.append({
            "tool_id": tool.id, "tool": tool.name, "sku": lic.sku, "seats_purchased": purchased,
            "seats_assigned": len(seats), "seats_active_30d": active,
            "utilization_pct": round(active / purchased * 100, 1) if purchased else 0,
            "monthly_cost_usd": purchased * price,
            "idle_cost_usd": (purchased - active) * price, "renewal_date": lic.renewal_date,
        })
    return out


@router.get("/inactive-seats")
def inactive_seats(tool_id: str, days: int = 30, p: Principal = Depends(require("licenses:read")),
                   db: Session = Depends(get_db)):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    q = (select(SeatAssignment, User).outerjoin(User, User.id == SeatAssignment.user_id)
         .where(SeatAssignment.tool_id == tool_id))
    if p.role == "dept_manager":
        q = q.where(User.department_id == p.department_id)
    return [{"user_id": s.user_id, "name": u.display_name if u else s.user_id,
             "department_id": u.department_id if u else None, "last_active_at": s.last_active_at}
            for s, u in db.execute(q) if not s.last_active_at or _aware(s.last_active_at) < cutoff]


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@router.get("/budgets")
def budgets(p: Principal = Depends(require("budgets:read")), db: Session = Depends(get_db)):
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    days_in_month, elapsed = (next_month - month_start).days, today.day
    col = {"department": E.department_id, "team": E.team_id, "tool": E.tool_id, "project": E.project}
    q = select(Budget)
    if p.role == "dept_manager":
        q = q.where(Budget.scope_type == "department", Budget.scope_id == p.department_id)
    out = []
    for b in db.scalars(q):
        conds = [E.ts >= datetime.combine(month_start, datetime.min.time(), UTC)]
        if b.scope_type in col:
            conds.append(col[b.scope_type] == b.scope_id)
        spent = _f(db.scalar(select(func.sum(E.cost_usd)).where(*conds)))
        limit = _f(b.monthly_limit_usd)
        forecast = spent / elapsed * days_in_month if elapsed else spent
        pct = spent / limit * 100 if limit else 0
        state = "over" if pct >= 100 else "warning" if pct >= b.alert_threshold_pct else \
            "at_risk" if forecast > limit else "ok"
        out.append({"id": b.id, "scope_type": b.scope_type, "scope_id": b.scope_id, "limit_usd": limit,
                    "spent_usd": round(spent, 2), "forecast_usd": round(forecast, 2), "used_pct": round(pct, 1),
                    "alert_threshold_pct": b.alert_threshold_pct, "state": state, "owner_email": b.owner_email})
    return out


@router.get("/chargeback.csv")
def chargeback(request: Request, f: Filters = Depends(), p: Principal = Depends(require("export:read")),
               db: Session = Depends(get_db)):
    """Monthly chargeback per department / cost centre / project / tool - feed into SAP / Oracle GL."""
    month = func.strftime("%Y-%m", E.ts) if engine.dialect.name == "sqlite" else func.to_char(E.ts, "YYYY-MM")
    rows = db.execute(
        select(month.label("month"), E.department_id, Department.cost_center, E.project, E.tool_id,
               func.sum(E.input_tokens + E.output_tokens).label("tokens"), func.sum(E.requests).label("req"),
               func.sum(E.cost_usd).label("cost"))
        .outerjoin(Department, Department.id == E.department_id)
        .where(*f.where(p)).group_by(month, E.department_id, Department.cost_center, E.project, E.tool_id)
        .order_by(month, E.department_id)
    ).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["month", "department", "cost_center", "project", "tool", "tokens", "requests", "cost_usd"])
    for r in rows:
        w.writerow([r.month, r.department_id, r.cost_center, r.project, r.tool_id, r.tokens, r.req,
                    round(_f(r.cost), 4)])
    audit(db, p.id, "export.chargeback", f"{f.start}..{f.end}", {"rows": len(rows)},
          ip=request.client.host if request.client else None)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=chargeback_{f.start}_{f.end}.csv"})


@router.get("/filters")
def filter_options(p: Principal = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    def distinct_vals(col):
        return [v for (v,) in db.execute(select(distinct(col)).where(*usage_scope(p), col.is_not(None)).order_by(col))]
    return {
        "providers": distinct_vals(E.provider),
        "tools": [{"id": t.id, "name": t.name} for t in db.scalars(select(AITool).order_by(AITool.name))],
        "departments": [{"id": d.id, "name": d.name} for d in db.scalars(select(Department).order_by(Department.name))]
        if p.role in ORG_WIDE_ROLES else [{"id": p.department_id, "name": p.department_id}],
        "models": distinct_vals(E.model),
    }
