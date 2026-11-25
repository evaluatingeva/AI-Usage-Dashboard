"""Anthropic collector (Claude API, Claude Code, Console).

Primary source: the Usage and Cost Analytics API (beta)
  GET /v1/organizations/analytics/usage   token + request counts
  GET /v1/organizations/analytics/cost    USD (decimal strings in CENTS)
  headers: x-api-key (Admin key, sk-ant-admin...), anthropic-version,
           anthropic-beta: analytics-usage-cost-2026-08-24
  Docs: https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api
  - starting_at must be >= 2026-01-01 and within 365 days; <= 31 days per request
  - time_bucket_width (1m|1h|1d) requires ending_at; we use 1h buckets
  - rows: {starting_at, ending_at, group{model, workspace_id, api_key_id, actor, product...}, usage{...}}
  - paging: has_more / next_page (pass back as `page`); HTTP 410 = cursor expired, restart
  - data refreshes within ~4h (up to 24h) and is revised until ~7 days after month end

Fallback: legacy Usage and Cost API (GET /v1/organizations/usage_report/messages), used
automatically when the analytics endpoints aren't available to the organization.
"""
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectorError, CollectResult, Collector

log = logging.getLogger("collectors.anthropic")

BASE = "https://api.anthropic.com/v1/organizations"
BETA = "analytics-usage-cost-2026-08-24"
API_VERSION = "2026-10-21"
EARLIEST = datetime(2026, 1, 1, tzinfo=UTC)
GROUP_BY = ["model", "workspace_id", "api_key_id", "actor", "product"]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AnthropicCollector(Collector):
    name = "anthropic"
    tool_id = "anthropic-api"
    provider = "anthropic"
    min_lookback_days = 7  # analytics data is revised for days; re-read a week every run

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.anthropic_admin_key)

    # ------------------------------------------------------------------ http
    def _key(self) -> str:
        # tolerate quotes/spaces pasted into .env
        return self.settings.anthropic_admin_key.strip().strip('"').strip("'").strip()

    def _headers(self, beta: bool) -> dict:
        h = {"x-api-key": self._key(), "anthropic-version": API_VERSION,
             "User-Agent": "ai-usage-monitor/1.1"}
        if beta:
            h["anthropic-beta"] = BETA
        return h

    def _get_pages(self, c: httpx.Client, path: str, params: list[tuple], beta: bool) -> list[dict]:
        out, page, restarts = [], None, 0
        while True:
            q = params + ([("page", page)] if page else [])
            r = c.get(f"{BASE}/{path}", params=q, headers=self._headers(beta))
            if r.status_code == 410 and restarts < 2:  # cursor expired after a data refresh
                out, page, restarts = [], None, restarts + 1
                continue
            if r.status_code >= 400:
                raise CollectorError.from_response("Anthropic", path, r)
            body = r.json()
            out.extend(body.get("data", []))
            if not body.get("has_more"):
                return out
            page = body.get("next_page")

    # --------------------------------------------------------------- collect
    def collect(self, since: datetime, db: Session) -> CollectResult:
        key = self._key()
        if not key.startswith("sk-ant-admin"):
            raise CollectorError(
                "ANTHROPIC_ADMIN_KEY is not an Admin API key. It must start with 'sk-ant-admin'. Create one in "
                "Claude Console > Settings > Admin keys (organization admins only). A normal 'sk-ant-api...' "
                "key cannot read usage.")
        aliases = self.alias_map(db)
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        start = max(min(since, now - timedelta(days=self.min_lookback_days)), EARLIEST,
                    now - timedelta(days=364))
        with self.http() as c:
            try:
                return self._collect_analytics(c, start, now, aliases)
            except CollectorError as exc:
                if exc.status == 404 or (exc.status == 403 and "not available" in str(exc).lower()):
                    log.warning("analytics API unavailable (%s); falling back to legacy usage report", exc)
                    return self._collect_legacy(c, start, now)
                raise

    def _windows(self, start: datetime, end: datetime):
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=30), end)
            yield cur, nxt
            cur = nxt

    def _collect_analytics(self, c, start, end, aliases) -> CollectResult:
        res = CollectResult()
        for w_start, w_end in self._windows(start, end):
            base = [("starting_at", _iso(w_start)), ("ending_at", _iso(w_end)), ("time_bucket_width", "1h"),
                    ("order_by", "time"), ("order", "asc"), ("limit", "1000")] + [("group_by[]", g) for g in GROUP_BY]
            usage = self._get_pages(c, "analytics/usage", base, beta=True)
            costs = self._get_pages(c, "analytics/cost", base, beta=True)
            cost_by_key = {self._row_key(r): float((r.get("cost") or {}).get("amount") or 0) / 100 for r in costs}
            for r in usage:
                g, u = r.get("group") or {}, r.get("usage") or {}
                actor = g.get("actor") or {}
                email = (actor.get("email_address") or actor.get("email") or "").lower()
                actor_id = actor.get("user_id") or actor.get("service_account_id") or actor.get("api_key_id")
                uid = aliases.get(email) or (aliases.get(str(actor_id).lower()) if actor_id else None) or email or None
                cache_create = sum((u.get("cache_creation") or {}).values())
                product = g.get("product") or ""
                res.events.append(UsageEventIn(
                    ts=_ts(r["starting_at"]),
                    tool_id="claude-code" if "claude_code" in product else self.tool_id,
                    provider=self.provider, model=g.get("model") or "code_execution", event_type="llm_call",
                    user_id=uid, project=g.get("workspace_id"),
                    input_tokens=(u.get("uncached_input_tokens") or 0) + cache_create,
                    cached_input_tokens=u.get("cache_read_input_tokens") or 0,
                    output_tokens=u.get("output_tokens") or 0, requests=u.get("requests") or 0,
                    cost_usd=cost_by_key.get(self._row_key(r)),
                    dedupe_key=f"anthropic:analytics:{self._row_key(r)[1]}",
                    attributes={"api_key_id": g.get("api_key_id"), "product": product or None,
                                "actor_type": actor.get("type"), "actor_id": actor_id,
                                "cache_creation_tokens": cache_create,
                                "web_search_requests": (u.get("server_tool_use") or {}).get("web_search_requests", 0)},
                ))
            # cost rows with no matching usage row (e.g. web search only) still count
            used = {self._row_key(r) for r in usage}
            for r in costs:
                k = self._row_key(r)
                if k not in used and cost_by_key.get(k):
                    g = r.get("group") or {}
                    res.events.append(UsageEventIn(
                        ts=_ts(r["starting_at"]), tool_id=self.tool_id, provider=self.provider,
                        model=g.get("model"), event_type="api_call", project=g.get("workspace_id"), requests=0,
                        cost_usd=cost_by_key[k], dedupe_key=f"anthropic:analytics-cost:{k[1]}",
                        attributes={"product": g.get("product")}))
        return res

    @staticmethod
    def _row_key(r: dict) -> tuple:
        g = r.get("group") or {}
        actor = g.get("actor") or {}
        actor_id = actor.get("user_id") or actor.get("service_account_id") or actor.get("api_key_id") or actor.get("id")
        dims = "|".join(str(x) for x in (g.get("model"), g.get("workspace_id"), g.get("api_key_id"),
                                          actor_id, g.get("product")))
        return r.get("starting_at"), dims

    def _collect_legacy(self, c, start, end) -> CollectResult:
        res = CollectResult()
        params = [("starting_at", _iso(start)), ("ending_at", _iso(end)), ("bucket_width", "1h"), ("limit", "168"),
                  ("group_by[]", "model"), ("group_by[]", "workspace_id"), ("group_by[]", "api_key_id")]
        for bucket in self._get_pages(c, "usage_report/messages", params, beta=False):
            ts = _ts(bucket["starting_at"])
            for r in bucket.get("results", []):
                cache_create = sum((r.get("cache_creation") or {}).values())
                key = f"{r.get('model')}|{r.get('workspace_id')}|{r.get('api_key_id')}"
                res.events.append(UsageEventIn(
                    ts=ts, tool_id=self.tool_id, provider=self.provider, model=r.get("model"), event_type="llm_call",
                    project=r.get("workspace_id"),
                    input_tokens=(r.get("uncached_input_tokens") or 0) + cache_create,
                    cached_input_tokens=r.get("cache_read_input_tokens") or 0,
                    output_tokens=r.get("output_tokens") or 0, requests=0,
                    dedupe_key=f"anthropic:legacy:{key}",
                    attributes={"api_key_id": r.get("api_key_id"), "cache_creation_tokens": cache_create}))
        return res
