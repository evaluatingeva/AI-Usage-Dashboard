"""Anthropic collector against a mocked Usage & Cost Analytics API, plus clean-mode seeding."""
import os
import tempfile
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/clean.db"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.collectors import anthropic_collector as ac  # noqa: E402
from app.collectors.base import CollectorError  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import License, UsageEvent, User  # noqa: E402

GROUP = {"model": "claude-opus-5-5", "workspace_id": "wrkspc_1", "api_key_id": "apikey_1",
         "actor": {"type": "user_actor", "user_id": "user_1", "email_address": "Jane@Firm.com"}, "product": "api"}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["anthropic-beta"] == ac.BETA
    assert request.headers["x-api-key"] == "sk-ant-admin01-test"
    if request.url.path.endswith("/analytics/usage"):
        if request.url.params.get("page") is None:
            return httpx.Response(200, json={"data": [{"starting_at": "2026-11-20T10:00:00Z", "ending_at": "2026-11-20T11:00:00Z",
                "group": GROUP, "usage": {"uncached_input_tokens": 1000, "cache_creation": {"ephemeral_5m_input_tokens": 200},
                                          "cache_read_input_tokens": 500, "output_tokens": 300, "requests": 7}}],
                "has_more": True, "next_page": "p2"})
        return httpx.Response(200, json={"data": [], "has_more": False, "next_page": None})
    if request.url.path.endswith("/analytics/cost"):
        return httpx.Response(200, json={"data": [{"starting_at": "2026-11-20T10:00:00Z", "group": GROUP,
                                                   "cost": {"currency": "USD", "amount": "1234.000000"}}], "has_more": False})
    return httpx.Response(404, json={"error": {"type": "not_found_error", "message": "nope"}})


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as s:
        yield s


def make(key, transport):
    c = ac.AnthropicCollector(Settings(anthropic_admin_key=key))
    c.http = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]
    return c


def test_analytics_rows_parsed(db):
    from datetime import UTC, datetime
    db.merge(User(id="jane@firm.com", display_name="Jane", email="jane@firm.com", role="viewer"))
    db.commit()
    res = make(' "sk-ant-admin01-test" ', httpx.MockTransport(handler)).collect(datetime(2026, 11, 19, tzinfo=UTC), db)
    assert len(res.events) == 1
    e = res.events[0]
    assert (e.input_tokens, e.cached_input_tokens, e.output_tokens, e.requests) == (1200, 500, 300, 7)
    assert e.user_id == "jane@firm.com" and e.cost_usd == pytest.approx(12.34) and e.model == "claude-opus-5-5"


def test_non_admin_key_gives_clear_error(db):
    from datetime import UTC, datetime
    with pytest.raises(CollectorError, match="sk-ant-admin"):
        make("sk-ant-api03-xyz", httpx.MockTransport(handler)).collect(datetime.now(UTC), db)


def test_401_message_includes_api_body(db):
    from datetime import UTC, datetime
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"type": "authentication_error",
                                                                          "message": "invalid x-api-key"}}))
    with pytest.raises(CollectorError) as exc:
        make("sk-ant-admin01-bad", t).collect(datetime.now(UTC), db)
    assert exc.value.status == 401 and "invalid x-api-key" in str(exc.value) and "ADMIN key" in str(exc.value)


def test_clean_mode_removes_demo(db, monkeypatch):
    from app import seed
    seed_users = seed.seed_reference(db)
    seed.seed_usage(db, seed_users, days=3)
    monkeypatch.setenv("ADMIN_EMAIL", "eva@company.com")
    admin = seed.clean_setup(db)
    assert admin == "eva@company.com"
    assert db.query(UsageEvent).filter(UsageEvent.source == "seed").count() == 0
    assert db.query(License).count() == 0
    assert db.query(User).filter(User.id.like("%@firm.example")).count() == 0
    assert db.get(User, "eva@company.com").role == "admin"


def test_overview_summary_with_only_anthropic_collector_data(db, monkeypatch):
    """Regression: Overview KPIs after --clean with real Anthropic rows, provider/tool filters, last 7 days."""
    from datetime import UTC, date, datetime, timedelta

    from fastapi.testclient import TestClient

    from app import seed
    from app.main import app
    from app.routers.analytics import _f, _pct

    monkeypatch.setenv("ADMIN_EMAIL", "eva@company.com")
    seed.clean_setup(db)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    t = httpx.MockTransport(lambda r: handler_at(r, now - timedelta(hours=3)))
    c = make("sk-ant-admin01-test", t)
    res = c.collect(now, db)
    from app.services.ingest import ingest_events
    ingest_events(db, res.events, source="collector:anthropic", upsert=True)
    db.commit()

    with TestClient(app) as client:
        tok = client.post("/api/auth/dev-login", json={"user_id": "eva@company.com"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        end = date.today()
        q = {"start": str(end - timedelta(days=6)), "end": str(end), "provider": "anthropic", "tool_id": "anthropic-api"}
        r = client.get("/api/analytics/summary", params=q, headers=h)
        assert r.status_code == 200, r.text
        cur = r.json()["current"]
        assert cur["input_tokens"] == 1200 and cur["cached_input_tokens"] == 500 and cur["output_tokens"] == 300 and cur["cost_usd"] == 12.34
        assert r.json()["change_pct"]["cost_usd"] is None  # no previous-period data yet

    assert _f(float("nan")) == 0.0 and _f("x") == 0.0 and _pct(5, 0) is None and _pct(3, 2) == 50.0


def handler_at(request: httpx.Request, ts: datetime) -> httpx.Response:
    """Same mock as `handler`, with buckets inside the dashboard's 7-day window."""
    r = handler(request)
    if r.status_code == 200:
        body = r.json()
        for row in body.get("data", []):
            row["starting_at"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            row["ending_at"] = (ts + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return httpx.Response(200, json=body)
    return r
