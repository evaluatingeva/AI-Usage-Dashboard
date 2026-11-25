"""Workday -> directory, through a Report-as-a-Service (RaaS) custom report.

Set-up in Workday (by the HRIS team):
1. Create an Integration System User (ISU) and a security group with *Get* access to
   Worker data, Organizations and Cost Centers. Exempt the ISU from password expiry.
2. Create an Advanced custom report on *All Active Workers* with the columns below and
   tick *Enable As Web Service*. Share it with the ISU.
3. Copy the JSON URL: Related actions -> Web Service -> View URLs -> JSON, e.g.
   https://wd5-services1.myworkday.com/ccx/service/customreport2/<tenant>/<owner>/AI_Usage_Directory?format=json

Default column aliases (override any with WORKDAY_FIELD_MAP='{"email":"Work_Email"}'):
  email=Email_Address, name=Worker, employee_id=Employee_ID, department=Supervisory_Organization,
  department_code=Department_Code, team=Team, cost_center=Cost_Center, manager_email=Manager_Email,
  active=Active, aliases=Other_IDs (semicolon separated: GitHub login, vendor user ids)
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..collectors.base import CollectorError, CollectResult, Collector
from .apply import PersonRecord, apply_directory

DEFAULT_FIELDS = {
    "email": "Email_Address", "name": "Worker", "employee_id": "Employee_ID",
    "department": "Supervisory_Organization", "department_code": "Department_Code", "team": "Team",
    "cost_center": "Cost_Center", "manager_email": "Manager_Email", "active": "Active", "aliases": "Other_IDs",
}


def _text(v) -> str | None:
    """Workday JSON gives plain strings, or {"Descriptor": "..."} / lists of those for references."""
    if v is None:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
        return _text(v)
    if isinstance(v, dict):
        return _text(v.get("Descriptor") or v.get("descriptor") or v.get("ID") or v.get("id"))
    s = str(v).strip()
    return s or None


def parse_report(payload: dict, field_map: dict | None = None) -> list[PersonRecord]:
    fm = {**DEFAULT_FIELDS, **(field_map or {})}
    out = []
    for row in payload.get("Report_Entry", []):
        def g(k):
            return _text(row.get(fm[k]))
        active = (g("active") or "1").lower() not in ("0", "false", "no", "inactive", "terminated")
        out.append(PersonRecord(
            email=g("email") or "", display_name=g("name"), employee_id=g("employee_id"),
            department=g("department"), department_code=g("department_code"), team=g("team"),
            cost_center=g("cost_center"), manager_email=g("manager_email"), active=active,
            aliases=[a for a in (g("aliases") or "").replace(",", ";").split(";") if a.strip()],
        ))
    return out


class WorkdayDirectory(Collector):
    name = "workday"
    kind = "directory"

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.workday_report_url)

    def collect(self, since: datetime, db: Session) -> CollectResult:
        s = self.settings
        headers = {"Accept": "application/json"}
        auth = None
        if s.workday_bearer_token:
            headers["Authorization"] = f"Bearer {s.workday_bearer_token}"
        elif s.workday_username:
            auth = (s.workday_username, s.workday_password)
        url = s.workday_report_url if "format=" in s.workday_report_url else (
            s.workday_report_url + ("&" if "?" in s.workday_report_url else "?") + "format=json")
        with self.http() as c:
            r = c.get(url, headers=headers, auth=auth)
        if r.status_code >= 400:
            raise CollectorError.from_response("Workday", "custom report", r, )
        try:
            payload = r.json()
        except ValueError as exc:
            raise CollectorError("Workday report did not return JSON: check the URL ends with ?format=json") from exc
        fm = json.loads(s.workday_field_map) if s.workday_field_map else None
        people = parse_report(payload, fm)
        if not people:
            raise CollectorError("Workday report returned 0 workers: check the report is shared with the ISU")
        st = apply_directory(db, people, "workday", deactivate_missing=s.directory_deactivate_missing,
                             reattribute_days=s.directory_reattribute_days)
        return CollectResult(records=st.people, note=_note(st))


def _note(st) -> str:
    return (f"{st.people} people ({st.created} new, {st.updated} changed, {st.deactivated} deactivated), "
            f"{st.departments} new departments, {st.teams} new teams, {st.events_reattributed} events re-attributed"
            + (f", {len(st.skipped)} rows without email skipped" if st.skipped else ""))
