"""Seat-based assistants: GitHub Copilot and Microsoft 365 Copilot.

Both produce:
 - SeatUpdate rows (who holds a seat, when they last used it) -> utilisation / idle seats
 - one `seat` event per assigned seat per day with the daily share of the seat price,
   attributed to the user -> lets seat costs flow into department chargeback.
"""
import calendar
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectResult, Collector, SeatUpdate


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _daily_seat_event(tool_id, provider, uid, price_month, day: datetime, active: bool) -> UsageEventIn:
    days = calendar.monthrange(day.year, day.month)[1]
    return UsageEventIn(
        ts=day, tool_id=tool_id, provider=provider, event_type="seat", user_id=uid, requests=0,
        quantity=1, unit_type="seat_day", cost_usd=round(price_month / days, 6),
        dedupe_key=f"{tool_id}:seat:{uid}", attributes={"active_that_day": active},
    )


class GitHubCopilotCollector(Collector):
    """GitHub REST: GET /orgs/{org}/copilot/billing/seats  and  /orgs/{org}/copilot/metrics.
    Token: fine-grained PAT or GitHub App with 'GitHub Copilot Business: read' (org)."""

    name = "github_copilot"
    tool_id = "github-copilot"
    provider = "github"

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.github_org and s.github_token)

    def collect(self, since: datetime, db: Session) -> CollectResult:
        h = {"Authorization": f"Bearer {self.settings.github_token}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        base = f"https://api.github.com/orgs/{self.settings.github_org}/copilot"
        aliases = self.alias_map(db)
        res = CollectResult(full_seat_snapshot_for=self.tool_id)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.http() as c:
            page = 1
            while True:
                r = c.get(f"{base}/billing/seats", params={"per_page": 100, "page": page}, headers=h)
                r.raise_for_status()
                seats = r.json().get("seats", [])
                for s in seats:
                    login = (s.get("assignee") or {}).get("login", "").lower()
                    uid = aliases.get(login, login)
                    last = _parse(s.get("last_activity_at"))
                    res.seats.append(SeatUpdate(self.tool_id, uid, last, _parse(s.get("created_at")),
                                                s.get("last_activity_editor")))
                    res.events.append(_daily_seat_event(self.tool_id, self.provider, uid,
                                                        self.settings.copilot_seat_price_usd, today,
                                                        bool(last and last >= today)))
                if len(seats) < 100:
                    break
                page += 1

            # Org-level daily engagement metrics (suggestions / acceptances / chats)
            r = c.get(f"{base}/metrics", params={"since": since.strftime("%Y-%m-%dT00:00:00Z")}, headers=h)
            if r.status_code == 200:
                for day in r.json():
                    sugg = acc = 0
                    for ed in (day.get("copilot_ide_code_completions") or {}).get("editors", []):
                        for m in ed.get("models", []):
                            for lang in m.get("languages", []):
                                sugg += lang.get("total_code_suggestions", 0)
                                acc += lang.get("total_code_acceptances", 0)
                    chats = sum(m.get("total_chats", 0)
                                for ed in (day.get("copilot_ide_chat") or {}).get("editors", [])
                                for m in ed.get("models", []))
                    res.events.append(UsageEventIn(
                        ts=_parse(day["date"] + "T00:00:00Z"), tool_id=self.tool_id, provider=self.provider,
                        event_type="api_call", requests=chats, quantity=sugg, unit_type="code_suggestion",
                        cost_usd=0, dedupe_key="github-copilot:metrics",
                        attributes={"acceptances": acc, "active_users": day.get("total_active_users"),
                                    "engaged_users": day.get("total_engaged_users")},
                    ))
        return res


class M365CopilotCollector(Collector):
    """Microsoft Graph usage report getMicrosoft365CopilotUsageUserDetail.
    App registration needs Reports.Read.All (application). NOTE: if the tenant setting
    'Display concealed user, group and site names in all reports' is ON, UPNs come back
    hashed - coordinate this with Privacy/Works Council before switching it off."""

    name = "m365_copilot"
    tool_id = "m365-copilot"
    provider = "microsoft"
    surfaces = ["microsoftTeams", "word", "excel", "powerPoint", "outlook", "oneNote", "loop", "copilotChat"]

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.ms_graph_tenant_id and s.ms_graph_client_id and s.ms_graph_client_secret)

    def collect(self, since: datetime, db: Session) -> CollectResult:
        aliases = self.alias_map(db)
        res = CollectResult(full_seat_snapshot_for=self.tool_id)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.http() as c:
            tok = c.post(f"https://login.microsoftonline.com/{self.settings.ms_graph_tenant_id}/oauth2/v2.0/token",
                         data={"client_id": self.settings.ms_graph_client_id,
                               "client_secret": self.settings.ms_graph_client_secret,
                               "scope": "https://graph.microsoft.com/.default",
                               "grant_type": "client_credentials"})
            tok.raise_for_status()
            h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
            url = ("https://graph.microsoft.com/beta/reports/"
                   "getMicrosoft365CopilotUsageUserDetail(period='D30')?$format=application/json")
            while url:
                r = c.get(url, headers=h)
                r.raise_for_status()
                body = r.json()
                for u in body.get("value", []):
                    upn = (u.get("userPrincipalName") or "").lower()
                    uid = aliases.get(upn, upn)
                    per_surface = {s: u.get(f"{s}CopilotLastActivityDate") for s in self.surfaces}
                    last_surface = max(((d, s) for s, d in per_surface.items() if d), default=(None, None))
                    last = _parse(u.get("lastActivityDate") + "T00:00:00Z") if u.get("lastActivityDate") else None
                    res.seats.append(SeatUpdate(self.tool_id, uid, last, None, last_surface[1]))
                    res.events.append(_daily_seat_event(self.tool_id, self.provider, uid,
                                                        self.settings.m365_copilot_seat_price_usd, today,
                                                        bool(last and last >= today - timedelta(days=1))))
                url = body.get("@odata.nextLink")
        return res
