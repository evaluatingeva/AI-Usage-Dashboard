"""Upsert people from an HR source and re-attribute usage events."""
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Department, Team, UsageEvent, User
from ..services.ingest import pseudonymise


@dataclass
class PersonRecord:
    email: str
    display_name: str | None = None
    department: str | None = None  # name or code, e.g. "Audit & Assurance"
    department_code: str | None = None  # stable id from HR if available, e.g. "AUD"
    team: str | None = None
    cost_center: str | None = None
    employee_id: str | None = None
    manager_email: str | None = None
    active: bool = True
    aliases: list[str] = field(default_factory=list)


@dataclass
class DirectoryStats:
    people: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    departments: int = 0
    teams: int = 0
    events_reattributed: int = 0
    skipped: list[str] = field(default_factory=list)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:64] or "unknown"


def apply_directory(db: Session, people: list[PersonRecord], source: str, deactivate_missing: bool = False,
                    reattribute_days: int = 90, restate: bool = False) -> DirectoryStats:
    """Upsert departments, teams and users. Roles are never changed by HR data (they are
    granted in the app or by OIDC app roles); new people start as `viewer`.

    reattribute_days: back-fill department/team on events of the last N days whose
    department is empty. restate=True also overwrites events that already have one
    (use after a re-org if chargeback should follow the new structure)."""
    st = DirectoryStats()
    depts = {d.id: d for d in db.scalars(select(Department))}
    teams = {t.id: t for t in db.scalars(select(Team))}
    users = {u.id.lower(): u for u in db.scalars(select(User))}
    seen: set[str] = set()

    for p in people:
        email = (p.email or "").strip().lower()
        if not email or "@" not in email:
            st.skipped.append(p.display_name or p.employee_id or "?")
            continue
        seen.add(email)
        st.people += 1
        dept_id = None
        if p.department or p.department_code:
            dept_id = slug(p.department_code or p.department or "")
            d = depts.get(dept_id)
            if d is None:
                d = Department(id=dept_id, name=(p.department or p.department_code or dept_id)[:200], cost_center=p.cost_center)
                db.add(d)
                depts[dept_id] = d
                st.departments += 1
            elif p.department and d.name != p.department[:200]:
                d.name = p.department[:200]
            if p.cost_center and not d.cost_center:
                d.cost_center = p.cost_center
        team_id = None
        if p.team and dept_id:
            team_id = slug(f"{dept_id}-{p.team}")
            if team_id not in teams:
                t = Team(id=team_id, name=p.team[:200], department_id=dept_id)
                db.add(t)
                teams[team_id] = t
                st.teams += 1
        aliases = {a.strip().lower() for a in p.aliases if a and a.strip()}
        if p.employee_id:
            aliases.add(str(p.employee_id).strip().lower())
        u = users.get(email)
        if u is None:
            u = User(id=email, display_name=(p.display_name or email)[:200], email=email, role="viewer",
                     department_id=dept_id, team_id=team_id, aliases=sorted(aliases), active=p.active)
            db.add(u)
            users[email] = u
            st.created += 1
        else:
            before = (u.display_name, u.department_id, u.team_id, u.active, sorted(u.aliases or []))
            u.display_name = (p.display_name or u.display_name)[:200]
            u.email = u.email or email
            u.department_id = dept_id or u.department_id
            u.team_id = team_id or (u.team_id if dept_id in (None, u.department_id) else None)
            u.active = p.active
            u.aliases = sorted(set(u.aliases or []) | aliases)
            if before != (u.display_name, u.department_id, u.team_id, u.active, sorted(u.aliases or [])):
                st.updated += 1
    db.flush()

    if deactivate_missing and seen:
        for key, u in users.items():
            if key not in seen and u.active and u.role == "viewer":
                u.active = False
                st.deactivated += 1

    st.events_reattributed = reattribute(db, reattribute_days, restate)
    db.commit()
    return st


def reattribute(db: Session, days: int = 90, restate: bool = False) -> int:
    """Fill department/team on recent events from the directory (by user id or alias)."""
    if days <= 0:
        return 0
    since = datetime.now(UTC) - timedelta(days=days)
    n = 0
    for u in db.scalars(select(User).where(User.department_id.is_not(None))):
        keys = {pseudonymise(u.id.lower())} | {u.id.lower()}
        conds = [UsageEvent.ts >= since, UsageEvent.user_id.in_(keys)]
        if not restate:
            conds.append(UsageEvent.department_id.is_(None))
        res = db.execute(update(UsageEvent).where(*conds)
                         .values(department_id=u.department_id, team_id=u.team_id)
                         .execution_options(synchronize_session=False))
        n += res.rowcount or 0
    return n
