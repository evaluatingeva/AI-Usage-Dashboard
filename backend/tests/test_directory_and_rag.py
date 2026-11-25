"""HR directory sync (CSV, Workday, Entra), re-attribution, per-user/department drill-down, RAG apps."""
import os
import tempfile
from datetime import UTC, datetime, timedelta

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/dir.db")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.directory.entra import parse_users  # noqa: E402
from app.directory.workday import WorkdayDirectory, parse_report  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas import UsageEventIn  # noqa: E402
from app.services.ingest import ingest_events  # noqa: E402

ADMIN = "dir-admin@company.com"


@pytest.fixture(scope="module")
def client():
    init_db()
    with SessionLocal() as db:
        if db.get(User, ADMIN) is None:
            db.add(User(id=ADMIN, display_name="Dir Admin", email=ADMIN, role="admin"))
            db.commit()
    with TestClient(app) as c:
        yield c


def hdr(c, uid):
    r = c.post("/api/auth/dev-login", json={"user_id": uid})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def ev(user, app_name=None, et="llm_call", ago=timedelta(minutes=5), **kw):
    ts = datetime.now(UTC) - ago
    kw.setdefault("model", "claude-sonnet-5")
    return UsageEventIn(ts=ts, tool_id="anthropic-api", provider="anthropic", event_type=et,
                        user_id=user, application=app_name, requests=kw.pop("requests", 1), **kw)


def test_csv_import_creates_departments_and_reattributes_old_events(client):
    with SessionLocal() as db:  # usage arrives BEFORE HR knows the person -> unattributed
        ingest_events(db, [ev("ana.tax@company.com", input_tokens=1000, output_tokens=200, cost_usd=3.0)], "test")
    csv = ("Work Email,Full Name,Department,Team,Cost Centre,Employee ID,Aliases\n"
           "Ana.Tax@company.com,Ana Tax,Tax Advisory,Indirect Tax,CC-410,E100,anatax-gh;user_abc\n"
           "ben.audit@company.com,Ben Audit,Audit & Assurance,FS Audit,CC-200,E200,\n"
           ",No Email,Audit & Assurance,,,E300,\n")
    h = hdr(client, ADMIN)
    r = client.post("/api/admin/directory/import", headers=h, files={"file": ("workday_export.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["people"] == 2 and st["skipped"] == 1 and st["events_reattributed"] >= 1
    with SessionLocal() as db:
        ana = db.get(User, "ana.tax@company.com")
        assert ana.department_id == "tax-advisory" and ana.team_id == "tax-advisory-indirect-tax"
        assert {"anatax-gh", "user_abc", "e100"} <= set(ana.aliases) and ana.role == "viewer"
    rows = client.get("/api/analytics/breakdown", headers=h, params={"dimension": "department_id"}).json()
    assert any(x["key"] == "tax-advisory" and x["label"] == "Tax Advisory" and x["cost_usd"] >= 3 for x in rows)


def test_department_and_user_profiles_and_dept_manager_scope(client):
    h = hdr(client, ADMIN)
    with SessionLocal() as db:
        ingest_events(db, [ev("ben.audit@company.com", input_tokens=500, output_tokens=50, cost_usd=1.5)], "test")
    d = client.get("/api/analytics/profile", headers=h, params={"dimension": "department_id", "key": "tax-advisory"}).json()
    assert d["info"]["label"] == "Tax Advisory" and d["info"]["headcount"] == 1
    assert d["top_users"][0]["key"] == "ana.tax@company.com" and d["by_team"][0]["label"] == "Indirect Tax"
    u = client.get("/api/analytics/profile", headers=h, params={"dimension": "user_id", "key": "ana.tax@company.com"}).json()
    assert u["info"]["department"] == "Tax Advisory" and u["info"]["cost_center"] == "CC-410"
    assert u["totals"]["cost_usd"] >= 3.0 and u["by_tool"][0]["key"] == "anthropic-api"

    # make Ana manager of Tax Advisory: she sees her department only
    r = client.put("/api/admin/users/ana.tax@company.com", headers=h, json={"role": "dept_manager"})
    assert r.status_code == 200, r.text
    ha = hdr(client, "ana.tax@company.com")
    keys = {x["key"] for x in client.get("/api/analytics/breakdown", headers=ha, params={"dimension": "department_id"}).json()}
    assert keys == {"tax-advisory"}
    other = client.get("/api/analytics/profile", headers=ha, params={"dimension": "user_id", "key": "ben.audit@company.com"}).json()
    assert other["totals"]["cost_usd"] == 0 and "department" not in other["info"]
    assert client.put("/api/admin/users/ben.audit@company.com", headers=ha, json={"role": "admin"}).status_code == 403


def test_rag_applications_running_and_inactive(client):
    with SessionLocal() as db:
        ingest_events(db, [
            ev("ana.tax@company.com", "Tax Research Assistant", "rag_retrieval", requests=40, documents_retrieved=200,
               vector_store="azure-ai-search", index_name="tax-kb", latency_ms=120),
            ev("ana.tax@company.com", "Tax Research Assistant", "embedding", model="text-embedding-3-large",
               input_tokens=8000, requests=40),
            ev("ana.tax@company.com", "Tax Research Assistant", "llm_call", input_tokens=30000, output_tokens=4000,
               requests=40, cost_usd=0.9),
            ev("ben.audit@company.com", "Audit Policy Bot", "vector_query", ago=timedelta(days=3), requests=5,
               vector_store="pgvector"),
        ], "test")
    h = hdr(client, ADMIN)
    apps = {a["application"]: a for a in client.get("/api/analytics/rag/apps", headers=h).json()}
    tra = apps["Tax Research Assistant"]
    assert tra["status"] == "running" and tra["queries"] == 40 and tra["documents"] == 200
    assert tra["embedding_tokens"] == 8000 and tra["llm_tokens"] == 34000 and tra["llm_calls"] == 40
    assert tra["vector_stores"] == ["azure-ai-search"] and tra["departments"] == ["Tax Advisory"]
    assert apps["Audit Policy Bot"]["status"] == "inactive"


def test_workday_and_entra_parsers_and_collector():
    payload = {"Report_Entry": [
        {"Email_Address": "c.consult@company.com", "Worker": "Cara Consult", "Employee_ID": "E9",
         "Supervisory_Organization": {"Descriptor": "Consulting - Tech"}, "Cost_Center": [{"Descriptor": "CC-700"}],
         "Team": "Cloud", "Active": "1", "Other_IDs": "cara-gh"},
        {"Email_Address": "old@company.com", "Worker": "Old Leaver", "Active": "0"}]}
    p = parse_report(payload)
    assert p[0].department == "Consulting - Tech" and p[0].cost_center == "CC-700" and p[0].aliases == ["cara-gh"]
    assert p[1].active is False
    e = parse_users([{"id": "oid-1", "displayName": "Dee", "mail": "dee@company.com", "userPrincipalName": "dee@company.com",
                      "department": "Deals", "employeeOrgData": {"costCenter": "CC-9", "division": "M&A"}}])
    assert e[0].department == "Deals" and e[0].team == "M&A" and e[0].aliases == ["oid-1"]

    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        return httpx.Response(200, json=payload)

    c = WorkdayDirectory(Settings(workday_report_url="https://wd5.example/ccx/service/customreport2/t/o/AI_Dir",
                                  workday_username="ISU_AI", workday_password="pw"))
    c.http = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    init_db()
    with SessionLocal() as db:
        res = c.collect(datetime.now(UTC), db)
        assert res.records == 2 and "2 people" in res.note
        assert seen["url"].endswith("?format=json") and seen["auth"].startswith("Basic ")
        assert db.get(User, "c.consult@company.com").department_id == "consulting-tech"
