"""End-to-end API tests against a throwaway SQLite database.
Run:  cd backend && pytest -q"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["AUTH_MODE"] = "dev"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import DEMO_INGEST_KEY, main as seed_main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    seed_main()
    with TestClient(app) as c:
        yield c


def login(c, user_id):
    r = c.post("/api/auth/dev-login", json={"user_id": user_id})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_ingest_requires_key(client):
    r = client.post("/api/ingest/events", json={"events": []})
    assert r.status_code == 401


def test_ingest_dedupe_and_costing(client):
    h = {"X-API-Key": DEMO_INGEST_KEY}
    ev = {"tool_id": "azure-openai", "provider": "azure_openai", "model": "gpt-4o", "user_id": "admin@firm.example",
          "input_tokens": 1_000_000, "output_tokens": 100_000, "dedupe_key": "test-1", "ts": "2026-01-15T10:00:00Z"}
    r = client.post("/api/ingest/events", json={"source": "sdk", "events": [ev]}, headers=h)
    assert r.json() == {"accepted": 1, "duplicates": 0}
    r = client.post("/api/ingest/events", json={"source": "sdk", "events": [ev]}, headers=h)
    assert r.json() == {"accepted": 0, "duplicates": 1}
    # cost = 1M * 2.5/M + 100k * 10/M = 3.5
    admin = login(client, "admin@firm.example")
    rows = client.get("/api/analytics/breakdown", params={"dimension": "model", "start": "2026-01-15",
                                                          "end": "2026-01-15"}, headers=admin).json()
    assert rows[0]["key"] == "gpt-4o" and abs(rows[0]["cost_usd"] - 3.5) < 1e-6


def test_prompt_content_rejected(client):
    ev = {"tool_id": "x", "provider": "x", "attributes": {"prompt": "secret client data"}}
    r = client.post("/api/ingest/events", json={"events": [ev]}, headers={"X-API-Key": DEMO_INGEST_KEY})
    assert r.status_code == 422


def test_rbac_scoping(client):
    admin = login(client, "admin@firm.example")
    mgr = login(client, "tax.lead@firm.example")
    org = client.get("/api/analytics/breakdown", params={"dimension": "department_id"}, headers=admin).json()
    dept = client.get("/api/analytics/breakdown", params={"dimension": "department_id"}, headers=mgr).json()
    assert len(org) >= 5
    assert [r["key"] for r in dept] == ["tax"]
    # managers can't read the audit log or create ingest keys
    assert client.get("/api/admin/audit", headers=mgr).status_code == 403
    assert client.post("/api/admin/ingest-keys", json={"name": "x"}, headers=mgr).status_code == 403


def test_viewer_sees_only_self(client):
    users = client.get("/api/auth/dev-users").json()
    viewer = next(u for u in users if u["role"] == "viewer")
    h = login(client, viewer["id"])
    rows = client.get("/api/analytics/breakdown", params={"dimension": "user_id"}, headers=h).json()
    assert {r["key"] for r in rows} <= {viewer["id"]}


def test_dashboard_endpoints(client):
    h = login(client, "finance@firm.example")
    s = client.get("/api/analytics/summary", headers=h).json()
    assert s["current"]["cost_usd"] > 0 and s["current"]["rag_queries"] > 0
    assert client.get("/api/analytics/timeseries", params={"group_by": "provider"}, headers=h).json()
    assert client.get("/api/analytics/rag", headers=h).json()["stores"]
    assert client.get("/api/analytics/non-token", headers=h).json()
    lic = client.get("/api/analytics/licenses", headers=h).json()
    assert {l["tool_id"] for l in lic} == {"github-copilot", "m365-copilot", "claude-code"}
    assert client.get("/api/analytics/budgets", headers=h).json()
    csv_resp = client.get("/api/analytics/chargeback.csv", headers=h)
    assert csv_resp.status_code == 200 and csv_resp.text.startswith("month,department")


def test_audit_chain(client):
    h = login(client, "auditor@firm.example")
    assert client.get("/api/admin/audit", headers=h).json()
    assert client.get("/api/admin/audit/verify", headers=h).json()["valid"] is True


def test_collector_upsert_replaces_bucket(client):
    from app.db import SessionLocal
    from app.schemas import UsageEventIn
    from app.services.ingest import ingest_events

    def ev(tokens):
        return UsageEventIn(ts="2026-02-01T00:00:00Z", tool_id="openai-api", provider="openai", model="gpt-4o",
                            input_tokens=tokens, dedupe_key="openai:completions:bucket-1")

    with SessionLocal() as db:
        assert ingest_events(db, [ev(100)], "collector:openai", upsert=True) == (1, 0)
        assert ingest_events(db, [ev(250)], "collector:openai", upsert=True) == (1, 0)
    h = login(client, "admin@firm.example")
    s = client.get("/api/analytics/summary", params={"start": "2026-02-01", "end": "2026-02-01"}, headers=h).json()
    assert s["current"]["input_tokens"] == 250
