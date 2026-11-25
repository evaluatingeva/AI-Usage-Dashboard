"""OpenAI (platform API) collector.

Uses the Organization Usage API with an *Admin key* (Settings -> Organization -> Admin keys):
  GET /v1/organization/usage/{completions|embeddings|images|audio_speeches|audio_transcriptions}
  GET /v1/organization/users     (maps OpenAI user ids -> emails)
Docs: https://platform.openai.com/docs/api-reference/usage
For ChatGPT Enterprise seat analytics use the Compliance/Analytics export -> CSV ingest.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectResult, Collector

BASE = "https://api.openai.com/v1/organization"

ENDPOINTS = {
    # endpoint: (event_type, unit_type, quantity field)
    "completions": ("llm_call", None, None),
    "embeddings": ("embedding", None, None),
    "images": ("image", "image", "images"),
    "audio_speeches": ("audio", "tts_character", "characters"),
    "audio_transcriptions": ("audio", "audio_second", "seconds"),
}


class OpenAICollector(Collector):
    name = "openai"
    tool_id = "openai-api"
    provider = "openai"

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.openai_admin_key)

    def _headers(self):
        return {"Authorization": f"Bearer {self.settings.openai_admin_key}"}

    def _get_all(self, client, path: str, params: dict) -> list[dict]:
        """Usage endpoints paginate with `page` / `next_page`."""
        out, page = [], None
        while True:
            q = {**params, **({"page": page} if page else {})}
            r = client.get(f"{BASE}/{path}", params=q, headers=self._headers())
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("data", []))
            if not body.get("has_more"):
                return out
            page = body.get("next_page")

    def _users(self, client) -> dict[str, str]:
        """Users endpoint paginates with `after` / `last_id`."""
        out, after = {}, None
        while True:
            q = {"limit": 100, **({"after": after} if after else {})}
            r = client.get(f"{BASE}/users", params=q, headers=self._headers())
            r.raise_for_status()
            body = r.json()
            out.update({u["id"]: (u.get("email") or "").lower() for u in body.get("data", [])})
            if not body.get("has_more"):
                return out
            after = body.get("last_id")

    def collect(self, since: datetime, db: Session) -> CollectResult:
        res = CollectResult()
        aliases = self.alias_map(db)
        with self.http() as c:
            users = self._users(c)
            for endpoint, (event_type, unit_type, qty_field) in ENDPOINTS.items():
                params = {"start_time": int(since.timestamp()), "bucket_width": "1d", "limit": 31,
                          "group_by": ["model", "project_id", "user_id", "api_key_id"]}
                for bucket in self._get_all(c, f"usage/{endpoint}", params):
                    ts = datetime.fromtimestamp(bucket["start_time"], UTC)
                    for r in bucket.get("results", []):
                        email = users.get(r.get("user_id") or "", "")
                        uid = aliases.get(email) or aliases.get((r.get("user_id") or "").lower()) or email or None
                        key = "|".join(str(r.get(k)) for k in ("model", "project_id", "user_id", "api_key_id"))
                        res.events.append(UsageEventIn(
                            ts=ts, tool_id=self.tool_id, provider=self.provider, model=r.get("model"),
                            event_type=event_type, user_id=uid, project=r.get("project_id"),
                            input_tokens=r.get("input_tokens", 0) or 0,
                            output_tokens=r.get("output_tokens", 0) or 0,
                            cached_input_tokens=r.get("input_cached_tokens", 0) or 0,
                            requests=r.get("num_model_requests", 0) or 0,
                            quantity=float(r.get(qty_field, 0) or 0) if qty_field else 0,
                            unit_type=unit_type,
                            dedupe_key=f"openai:{endpoint}:{key}",
                            attributes={"api_key_id": r.get("api_key_id"), "batch": r.get("batch")},
                        ))
        return res
