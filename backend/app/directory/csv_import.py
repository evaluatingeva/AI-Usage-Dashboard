"""Directory from a CSV export of any HR system (SuccessFactors, Oracle HCM, ADP, Workday ad-hoc export).

Recognised headers (case-insensitive; first match wins):
  email | work_email | mail | upn           -> email (required)
  name | display_name | worker | full_name  -> display name
  department | department_name | org        -> department
  department_code | dept_code               -> stable department id
  team | sub_department | division          -> team
  cost_center | cost_centre                 -> cost centre
  employee_id | employee_number             -> stored as alias
  aliases | other_ids                       -> ';' separated (GitHub login, OpenAI user id, ...)
  active | status                           -> 0/false/terminated = inactive
"""
import csv
import io

from .apply import PersonRecord

COLS = {
    "email": ["email", "work_email", "mail", "upn", "email_address"],
    "display_name": ["name", "display_name", "worker", "full_name"],
    "department": ["department", "department_name", "org", "supervisory_organization"],
    "department_code": ["department_code", "dept_code"],
    "team": ["team", "sub_department", "division"],
    "cost_center": ["cost_center", "cost_centre"],
    "employee_id": ["employee_id", "employee_number", "worker_id"],
    "aliases": ["aliases", "other_ids"],
    "active": ["active", "status"],
}


def parse_csv(text: str) -> list[PersonRecord]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {h.strip().lower().replace(" ", "_"): h for h in (reader.fieldnames or [])}
    pick = {k: next((headers[c] for c in cands if c in headers), None) for k, cands in COLS.items()}
    if not pick["email"]:
        raise ValueError("CSV needs an 'email' column (also accepted: work_email, mail, upn)")
    out = []
    for row in reader:
        def g(k):
            col = pick[k]
            v = (row.get(col) or "").strip() if col else ""
            return v or None
        out.append(PersonRecord(
            email=g("email") or "", display_name=g("display_name"), department=g("department"),
            department_code=g("department_code"), team=g("team"), cost_center=g("cost_center"),
            employee_id=g("employee_id"),
            active=(g("active") or "1").lower() not in ("0", "false", "no", "inactive", "terminated"),
            aliases=[a for a in (g("aliases") or "").split(";") if a.strip()],
        ))
    return out
