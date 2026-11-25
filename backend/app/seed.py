"""Seed reference data + 90 days of realistic DEMO usage so the dashboard has
something to show. Run:  python -m app.seed  [--no-demo-usage]

Prices below are ILLUSTRATIVE public list prices - replace them with your
negotiated enterprise rates before using the numbers for chargeback.
"""
import os
import random
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, update

from .auth import hash_key
from .db import SessionLocal, init_db
from .models import (
    AITool, Budget, Department, IngestKey, License, PriceCatalog, SeatAssignment, Team, UsageEvent, User,
)
from .schemas import UsageEventIn
from .services.audit import audit
from .services.ingest import ingest_events

M = 1_000_000
DEMO_INGEST_KEY = "aiu_demo_local_only_key_change_me"

DEPARTMENTS = [
    ("audit", "Audit & Assurance", "CC-1000", ["Financial Audit", "IT Audit"]),
    ("tax", "Tax & Legal", "CC-2000", ["Corporate Tax", "Transfer Pricing"]),
    ("consulting", "Consulting", "CC-3000", ["Technology Consulting", "Strategy"]),
    ("advisory", "Risk & Deal Advisory", "CC-4000", ["Forensics", "M&A"]),
    ("it", "Internal IT & Data", "CC-9000", ["AI Platform", "Service Desk"]),
]

TOOLS = [
    ("openai-api", "OpenAI API", "OpenAI", "llm_api", "usage"),
    ("azure-openai", "Azure OpenAI", "Microsoft", "llm_api", "usage"),
    ("anthropic-api", "Claude API", "Anthropic", "llm_api", "usage"),
    ("claude-code", "Claude Code", "Anthropic", "assistant_seat", "hybrid"),
    ("aws-bedrock", "Amazon Bedrock", "AWS", "llm_api", "usage"),
    ("google-gemini", "Gemini / Vertex AI", "Google", "llm_api", "usage"),
    ("github-copilot", "GitHub Copilot Business", "GitHub", "assistant_seat", "seat"),
    ("m365-copilot", "Microsoft 365 Copilot", "Microsoft", "assistant_seat", "seat"),
    ("azure-ai-search", "Azure AI Search (RAG)", "Microsoft", "vector_db", "usage"),
    ("pgvector", "pgvector knowledge base", "Internal", "vector_db", "usage"),
    ("internal-apps", "Internal AI applications", "Internal", "rag", "usage"),
    ("ai-gateway", "LiteLLM AI gateway", "Internal", "llm_api", "usage"),
]

PRICES = [  # provider, model(pattern), unit_type, usd per unit
    ("openai", "gpt-4o*", "input_tokens", 2.5 / M), ("openai", "gpt-4o*", "output_tokens", 10 / M),
    ("openai", "gpt-4o*", "cached_input_tokens", 1.25 / M),
    ("openai", "gpt-4o-mini*", "input_tokens", 0.15 / M), ("openai", "gpt-4o-mini*", "output_tokens", 0.6 / M),
    ("openai", "text-embedding-3-large", "input_tokens", 0.13 / M),
    ("openai", "gpt-image-1", "image", 0.04), ("openai", "whisper-1", "audio_minute", 0.006),
    ("azure_openai", "gpt-4o*", "input_tokens", 2.5 / M), ("azure_openai", "gpt-4o*", "output_tokens", 10 / M),
    ("azure_openai", "gpt-4o-mini*", "input_tokens", 0.15 / M),
    ("azure_openai", "gpt-4o-mini*", "output_tokens", 0.6 / M),
    ("azure_openai", "text-embedding-3-large", "input_tokens", 0.13 / M),
    ("azure_openai", "whisper*", "audio_minute", 0.006),
    ("anthropic", "claude-sonnet-4*", "input_tokens", 3 / M), ("anthropic", "claude-sonnet-4*", "output_tokens", 15 / M),
    ("anthropic", "claude-sonnet-4*", "cached_input_tokens", 0.3 / M),
    ("anthropic", "claude-haiku-4*", "input_tokens", 1 / M), ("anthropic", "claude-haiku-4*", "output_tokens", 5 / M),
    ("anthropic", "claude-sonnet-4-6*", "input_tokens", 3 / M), ("anthropic", "claude-sonnet-4-6*", "output_tokens", 15 / M),
    ("anthropic", "claude-sonnet-5*", "input_tokens", 2 / M), ("anthropic", "claude-sonnet-5*", "output_tokens", 10 / M),
    ("anthropic", "claude-sonnet-5-5*", "cached_input_tokens", 0.1 / M),
    ("anthropic", "claude-opus-4*", "input_tokens", 5 / M), ("anthropic", "claude-opus-4*", "output_tokens", 25 / M),
    ("anthropic", "claude-opus-5*", "input_tokens", 5 / M), ("anthropic", "claude-opus-5*", "output_tokens", 25 / M),
    ("anthropic", "claude-opus-5-5*", "input_tokens", 4 / M), ("anthropic", "claude-opus-5-5*", "output_tokens", 20 / M),
    ("anthropic", "claude-opus-5-5*", "cached_input_tokens", 0.2 / M),
    ("anthropic", "claude-haiku-5-5*", "input_tokens", 0.1 / M), ("anthropic", "claude-haiku-5-5*", "output_tokens", 0.5 / M),
    ("anthropic", "claude-fable-5*", "input_tokens", 10 / M), ("anthropic", "claude-fable-5*", "output_tokens", 50 / M),
    ("anthropic", "claude-fable-5-2*", "input_tokens", 8 / M), ("anthropic", "claude-fable-5-2*", "output_tokens", 40 / M),
    ("bedrock", "anthropic.claude-sonnet-4*", "input_tokens", 3 / M),
    ("bedrock", "anthropic.claude-sonnet-4*", "output_tokens", 15 / M),
    ("bedrock", "amazon.titan-embed*", "input_tokens", 0.02 / M),
    ("google", "gemini-2.5-pro*", "input_tokens", 1.25 / M), ("google", "gemini-2.5-pro*", "output_tokens", 10 / M),
    ("google", "gemini-2.5-flash*", "input_tokens", 0.3 / M), ("google", "gemini-2.5-flash*", "output_tokens", 2.5 / M),
    ("azure-ai-search", "*", "query", 0.0004), ("pgvector", "*", "query", 0.00005),
]

LLM_MIX = [  # tool, provider, model, weight, avg_in, avg_out
    ("azure-openai", "azure_openai", "gpt-4o", 30, 2400, 420),
    ("azure-openai", "azure_openai", "gpt-4o-mini", 25, 1200, 250),
    ("anthropic-api", "anthropic", "claude-sonnet-4-5", 18, 3200, 650),
    ("anthropic-api", "anthropic", "claude-haiku-4-5", 8, 1500, 300),
    ("openai-api", "openai", "gpt-4o", 8, 2000, 400),
    ("aws-bedrock", "bedrock", "anthropic.claude-sonnet-4-5", 6, 2800, 500),
    ("google-gemini", "google", "gemini-2.5-pro", 3, 4000, 700),
    ("google-gemini", "google", "gemini-2.5-flash", 2, 1800, 300),
]
APPS = ["Audit Workpaper Assistant", "Tax Research Copilot", "Contract Analyzer", "Proposal Writer", "Due Diligence Bot"]


def seed_reference(db) -> list[User]:
    rnd = random.Random(7)
    for did, name, cc, teams in DEPARTMENTS:
        db.merge(Department(id=did, name=name, cost_center=cc))
        for t in teams:
            db.merge(Team(id=f"{did}-{t.lower().replace(' ', '-').replace('&', 'and')}", name=t, department_id=did))
    db.flush()
    users = [
        User(id="admin@firm.example", display_name="Avery Admin", email="admin@firm.example", role="admin",
             department_id="it", team_id="it-ai-platform"),
        User(id="finance@firm.example", display_name="Fin Controller", email="finance@firm.example",
             role="finance", department_id="it", team_id="it-service-desk"),
        User(id="auditor@firm.example", display_name="Ira Auditor", email="auditor@firm.example",
             role="auditor", department_id="advisory", team_id="advisory-forensics"),
        User(id="tax.lead@firm.example", display_name="Tara Taxlead", email="tax.lead@firm.example",
             role="dept_manager", department_id="tax", team_id="tax-corporate-tax"),
    ]
    first = ["Alex", "Sam", "Jordan", "Priya", "Chen", "Maria", "Omar", "Lena", "Ravi", "Noah", "Aisha", "Tom"]
    last = ["Shah", "Smith", "Kumar", "Garcia", "Muller", "Okafor", "Rossi", "Ito", "Brown", "Novak"]
    all_teams = [(d[0], t) for d in DEPARTMENTS for t in d[3]]
    for i in range(56):
        did, t = all_teams[i % len(all_teams)]
        fn, ln = rnd.choice(first), rnd.choice(last)
        uid = f"{fn.lower()}.{ln.lower()}{i}@firm.example"
        users.append(User(id=uid, display_name=f"{fn} {ln}", email=uid, role="viewer", department_id=did,
                          team_id=f"{did}-{t.lower().replace(' ', '-').replace('&', 'and')}",
                          aliases=[f"gh-{fn.lower()}{i}"]))
    for u in users:
        db.merge(u)
    for tid, name, vendor, cat, billing in TOOLS:
        db.merge(AITool(id=tid, name=name, vendor=vendor, category=cat, billing_model=billing,
                        data_classification="confidential" if cat == "llm_api" else "internal",
                        owner="ai-platform@firm.example"))
    db.flush()
    ensure_catalogue(db)
    if not db.scalar(select(License).limit(1)):
        db.add_all([
            License(tool_id="github-copilot", sku="Copilot Business", seats_purchased=40,
                    price_per_seat_month_usd=19, renewal_date=date(2027, 3, 31)),
            License(tool_id="m365-copilot", sku="Microsoft 365 Copilot", seats_purchased=50,
                    price_per_seat_month_usd=30, renewal_date=date(2027, 6, 30)),
            License(tool_id="claude-code", sku="Claude Team Premium", seats_purchased=15,
                    price_per_seat_month_usd=150, renewal_date=date(2027, 1, 31)),
        ])
    if not db.scalar(select(Budget).limit(1)):
        db.add_all([
            Budget(scope_type="org", scope_id="firm", monthly_limit_usd=2500, owner_email="finance@firm.example"),
            Budget(scope_type="department", scope_id="tax", monthly_limit_usd=105, owner_email="tax.lead@firm.example"),
            Budget(scope_type="department", scope_id="consulting", monthly_limit_usd=140),
            Budget(scope_type="department", scope_id="audit", monthly_limit_usd=300),
            Budget(scope_type="department", scope_id="advisory", monthly_limit_usd=600),
            Budget(scope_type="tool", scope_id="anthropic-api", monthly_limit_usd=200, alert_threshold_pct=75),
        ])
    if not db.scalar(select(IngestKey).where(IngestKey.name == "demo-local")):
        db.add(IngestKey(name="demo-local", key_prefix=DEMO_INGEST_KEY[:12], key_hash=hash_key(DEMO_INGEST_KEY),
                         created_by="seed"))
    db.commit()
    return users


def seed_usage(db, users: list[User], days: int = 90) -> int:
    rnd = random.Random(42)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    viewers = [u for u in users if u.role == "viewer"] + users[:4]
    heavy = set(rnd.sample([u.id for u in viewers], 10))
    events: list[UsageEventIn] = []
    weights = [m[3] for m in LLM_MIX]
    for d in range(days, -1, -1):
        day = now - timedelta(days=d)
        weekday = day.weekday() < 5
        growth = 0.55 + 0.45 * (days - d) / days  # adoption grows over the quarter
        for u in viewers:
            if not weekday and rnd.random() > 0.15:
                continue
            calls = int(rnd.gauss(6 if u.id in heavy else 2.2, 1.2) * growth)
            for _ in range(max(calls, 0)):
                tool, prov, model, _, ai, ao = rnd.choices(LLM_MIX, weights)[0]
                ts = day.replace(hour=rnd.randint(7, 19), minute=rnd.randint(0, 59))
                app = rnd.choice(APPS)
                proj = f"ENG-{rnd.randint(1000, 1040)}"
                n = rnd.randint(8, 45)  # each event = an hourly aggregate of calls
                inp = int(abs(rnd.gauss(ai, ai * 0.4)) * n)
                events.append(UsageEventIn(ts=ts, tool_id=tool, provider=prov, model=model, user_id=u.id,
                                           application=app, project=proj, requests=n, input_tokens=inp,
                                           cached_input_tokens=int(inp * rnd.choice([0, 0, 0.2, 0.4])),
                                           output_tokens=int(abs(rnd.gauss(ao, ao * 0.4)) * n),
                                           latency_ms=abs(rnd.gauss(1800, 600))))
                if app in ("Tax Research Copilot", "Audit Workpaper Assistant", "Due Diligence Bot"):
                    store = "azure-ai-search" if app != "Due Diligence Bot" else "pgvector"
                    idx = {"Tax Research Copilot": "tax-guidance", "Audit Workpaper Assistant": "audit-methodology",
                           "Due Diligence Bot": "deal-dataroom"}[app]
                    events.append(UsageEventIn(ts=ts, tool_id=store, provider=store, event_type="rag_retrieval",
                                               user_id=u.id, application=app, project=proj, requests=n,
                                               quantity=n, unit_type="query", vector_store=store, index_name=idx,
                                               documents_retrieved=n * rnd.choice([3, 5, 8, 10]),
                                               latency_ms=abs(rnd.gauss(120 if store == "pgvector" else 240, 60))))
                    events.append(UsageEventIn(ts=ts, tool_id="azure-openai", provider="azure_openai",
                                               model="text-embedding-3-large", event_type="embedding",
                                               user_id=u.id, application=app, project=proj, requests=n,
                                               input_tokens=n * rnd.randint(15, 60)))
            if weekday and rnd.random() < 0.05:
                events.append(UsageEventIn(ts=day.replace(hour=11), tool_id="openai-api", provider="openai",
                                           model="gpt-image-1", event_type="image", user_id=u.id,
                                           quantity=rnd.randint(1, 6), unit_type="image", application="Proposal Writer"))
            if weekday and rnd.random() < 0.08:
                events.append(UsageEventIn(ts=day.replace(hour=15), tool_id="azure-openai", provider="azure_openai",
                                           model="whisper", event_type="audio", user_id=u.id,
                                           quantity=round(rnd.uniform(5, 60), 1), unit_type="audio_minute",
                                           application="Meeting Transcriber"))
        # nightly batch re-indexing of the knowledge bases (no user - attributed to IT)
        events.append(UsageEventIn(ts=day.replace(hour=2), tool_id="azure-openai", provider="azure_openai",
                                   model="text-embedding-3-large", event_type="embedding", department_id="it",
                                   team_id="it-ai-platform", application="KB Indexer", requests=rnd.randint(200, 900),
                                   input_tokens=rnd.randint(2, 9) * 1_000_000))
    accepted, _ = ingest_events(db, events, source="seed")

    # seats
    cutoff_rnd = random.Random(3)
    db.query(SeatAssignment).delete()
    for tool, n in (("github-copilot", 34), ("m365-copilot", 46), ("claude-code", 12)):
        pool = [u for u in viewers if tool != "github-copilot" or u.department_id in ("consulting", "it", "audit")]
        for u in cutoff_rnd.sample(pool, min(n, len(pool))):
            idle = cutoff_rnd.random() < 0.28
            last = now - timedelta(days=cutoff_rnd.randint(31, 120) if idle else cutoff_rnd.randint(0, 12))
            db.add(SeatAssignment(tool_id=tool, user_id=u.id, assigned_at=now - timedelta(days=150),
                                  last_active_at=None if idle and cutoff_rnd.random() < 0.3 else last,
                                  last_surface=cutoff_rnd.choice(["vscode", "teams", "word", "outlook", "excel"])))
    db.commit()
    return accepted


DEMO_DOMAIN = "@firm.example"
DEMO_LICENSE_SKUS = ("Copilot Business", "Microsoft 365 Copilot", "Claude Team Premium")


def ensure_catalogue(db, owner: str | None = None) -> None:
    """Tool catalogue + price catalogue only. Adds missing rows, never overwrites yours."""
    for tid, name, vendor, cat, billing in TOOLS:
        if db.get(AITool, tid) is None:
            db.add(AITool(id=tid, name=name, vendor=vendor, category=cat, billing_model=billing,
                          data_classification="confidential" if cat == "llm_api" else "internal", owner=owner))
    have = {(r.provider, r.model, r.unit_type) for r in db.scalars(select(PriceCatalog))}
    db.add_all(PriceCatalog(provider=p, model=m, unit_type=u, price_per_unit_usd=v)
               for p, m, u, v in PRICES if (p, m, u) not in have)
    db.commit()


def purge_demo(db) -> dict:
    """Remove every row the demo seed created. Real data (collector, SDK, CSV events,
    your own licences/budgets/users) is left alone. The audit log is append-only and is kept."""
    demo_users = [u.id for u in db.scalars(select(User).where(User.id.like(f"%{DEMO_DOMAIN}")))]
    out = {
        "usage_events": db.execute(delete(UsageEvent).where(UsageEvent.source == "seed")).rowcount,
        "seat_assignments": db.execute(delete(SeatAssignment).where(
            SeatAssignment.user_id.like(f"%{DEMO_DOMAIN}"))).rowcount,
        "licenses": db.execute(delete(License).where(License.sku.in_(DEMO_LICENSE_SKUS))).rowcount,
        "budgets": db.execute(delete(Budget).where(
            (Budget.owner_email.like(f"%{DEMO_DOMAIN}")) |
            (Budget.scope_id.in_(["firm", "tax", "consulting", "audit", "advisory", "anthropic-api"])
             & Budget.monthly_limit_usd.in_([2500, 105, 140, 300, 600, 200, 6000, 900, 1500, 1200])))).rowcount,
        "ingest_keys": db.execute(delete(IngestKey).where(IngestKey.name == "demo-local")).rowcount,
        "users": db.execute(delete(User).where(User.id.in_(demo_users))).rowcount if demo_users else 0,
    }
    demo_depts = [d[0] for d in DEPARTMENTS]
    out["teams"] = db.execute(delete(Team).where(Team.department_id.in_(demo_depts))).rowcount
    out["departments"] = db.execute(delete(Department).where(Department.id.in_(demo_depts))).rowcount
    db.execute(update(AITool).where(AITool.owner.like(f"%{DEMO_DOMAIN}")).values(owner=None))
    db.commit()
    return out


def clean_setup(db) -> str:
    """Production-style start: catalogue + prices + one admin. No departments, users,
    licences, budgets or ingest keys - add your own through the UI or API."""
    admin_id = os.environ.get("ADMIN_EMAIL", "admin@local").strip().lower()
    removed = purge_demo(db)
    print("removed demo rows:", {k: v for k, v in removed.items() if v} or "none")
    ensure_catalogue(db)
    if db.get(User, admin_id) is None:
        db.add(User(id=admin_id, display_name=admin_id.split("@")[0].title(), email=admin_id, role="admin"))
    else:
        db.get(User, admin_id).role = "admin"
    db.commit()
    return admin_id


def main() -> None:
    init_db()
    with SessionLocal() as db:
        if "--clean" in sys.argv or "--purge-demo" in sys.argv:
            admin_id = clean_setup(db)
            audit(db, "system:seed", "seed.clean", None, {"admin": admin_id})
            print(f"clean setup done. Sign in as: {admin_id} (role admin)")
            print("Next: add licences, budgets, departments and an ingest key in the UI or via /api/admin.")
            return
        users = seed_reference(db)
        print(f"reference data ready ({len(users)} users)")
        if "--no-demo-usage" not in sys.argv:
            n = seed_usage(db, users)
            print(f"inserted {n} demo usage events")
        audit(db, "system:seed", "seed.run", None, {"demo_usage": "--no-demo-usage" not in sys.argv})
        print(f"demo ingest key: {DEMO_INGEST_KEY}  (local only - revoke in any shared environment)")


if __name__ == "__main__":
    main()
