"""Entra ID (Azure AD) -> directory via Microsoft Graph.

Most Big 4 firms already sync department, cost centre and employee id from Workday/SAP
into Entra ID, so this is often the quickest route. Needs Microsoft Graph application
permission **User.Read.All** (admin consent) on the same app registration as the
M365 Copilot collector. The Entra object id is stored as an alias, so Azure APIM
events that carry the caller's `oid` are attributed to the right person.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from ..collectors.base import CollectorError, CollectResult, Collector
from .apply import PersonRecord, apply_directory
from .workday import _note

SELECT = "id,displayName,mail,userPrincipalName,department,jobTitle,employeeId,accountEnabled,employeeOrgData"


def parse_users(items: list[dict]) -> list[PersonRecord]:
    out = []
    for u in items:
        email = u.get("mail") or u.get("userPrincipalName") or ""
        org = u.get("employeeOrgData") or {}
        out.append(PersonRecord(
            email=email, display_name=u.get("displayName"), employee_id=u.get("employeeId"),
            department=u.get("department"), team=org.get("division"), cost_center=org.get("costCenter"),
            active=bool(u.get("accountEnabled", True)),
            aliases=[a for a in (u.get("id"), u.get("userPrincipalName")) if a and a.lower() != email.lower()],
        ))
    return out


class EntraDirectory(Collector):
    name = "entra_directory"
    kind = "directory"

    @classmethod
    def enabled(cls, s) -> bool:
        return s.entra_directory_sync and bool(s.ms_graph_tenant_id and s.ms_graph_client_id)

    def _token(self, c) -> str:
        s = self.settings
        r = c.post(f"https://login.microsoftonline.com/{s.ms_graph_tenant_id}/oauth2/v2.0/token", data={
            "client_id": s.ms_graph_client_id, "client_secret": s.ms_graph_client_secret,
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
        if r.status_code >= 400:
            raise CollectorError.from_response("Entra ID", "token", r)
        return r.json()["access_token"]

    def collect(self, since: datetime, db: Session) -> CollectResult:
        items: list[dict] = []
        with self.http() as c:
            h = {"Authorization": f"Bearer {self._token(c)}", "ConsistencyLevel": "eventual"}
            url = f"https://graph.microsoft.com/v1.0/users?$select={SELECT}&$filter=userType eq 'Member'&$count=true&$top=999"
            while url:
                r = c.get(url, headers=h)
                if r.status_code >= 400:
                    raise CollectorError.from_response("Microsoft Graph", "/users", r)
                body = r.json()
                items += body.get("value", [])
                url = body.get("@odata.nextLink")
        s = self.settings
        st = apply_directory(db, parse_users(items), "entra", deactivate_missing=s.directory_deactivate_missing,
                             reattribute_days=s.directory_reattribute_days)
        return CollectResult(records=st.people, note=_note(st))
