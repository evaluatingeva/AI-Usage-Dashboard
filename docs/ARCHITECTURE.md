# Architecture

## Components

| Component | Tech | Responsibility |
|---|---|---|
| `api` | FastAPI (uvicorn) | Ingestion API, analytics API, admin API, auth/RBAC, audit |
| `worker` | APScheduler | Runs vendor collectors hourly; nightly retention purge |
| `db` | PostgreSQL 16 + TimescaleDB | `usage_events` hypertable (7-day chunks, compressed after 30 days), reference tables, audit log |
| `web` | React + Vite, served by nginx | Dashboard; proxies `/api` to the API |
| `aiusage` SDK | Python, zero dependencies | Push metering from internal apps / RAG pipelines |
| Gateway callback | LiteLLM `CustomLogger` | Per-call metering for all traffic through the AI gateway |

## Ingestion pipeline (`app/services/ingest.py`)

1. **Validate** against `UsageEventIn` (types, non-negative counts, no content keys).
2. **Enrich**: look up `user_id` in the directory to fill `department_id` and `team_id` when the source doesn't send them.
3. **Pseudonymise** user ids with keyed HMAC-SHA256 when enabled.
4. **Price**: if the source didn't send `cost_usd`, compute from `price_catalog` (exact model match first, then the longest `prefix*` pattern; effective-dated). `cost_source` records whether cost came from the provider, was computed, or is unknown.
5. **De-duplicate** on `(dedupe_key, ts)`. SDK and API events skip duplicates; collectors use *upsert* so a growing "today" bucket is replaced on each run.
6. **Bulk insert** with SQLAlchemy Core in 1,000-row batches.

## Identity resolution

Vendors identify people differently: OpenAI user ids, GitHub logins, Graph UPNs, AWS role ARNs. Each
collector resolves through `users.aliases` plus email. Unresolved identities stay as-is, are visible
as "unattributed" in breakdowns, and should be fixed in the directory sync rather than in code.

## Attribution hierarchy

`user → team → department → cost centre` comes from the directory. `project` carries the engagement or
client code (send it from apps or gateway metadata; for vendor data use workspace/project ids mapped to
engagements). `application` names the internal app.

## Scaling notes

- A firm of ~50k staff generating ~5M events/day is comfortably within one TimescaleDB node; enable
  compression and query the `usage_daily` continuous aggregate for ranges over ~90 days.
- Ingest is stateless; scale the `api` horizontally. For very high volume put Azure Event Hubs /
  Kafka in front and run a consumer that calls `ingest_events` in batches.
- Collectors are idempotent and safe to re-run; keep one worker replica (or add a DB advisory lock)
  to avoid concurrent runs of the same collector.

## Extending

- **New vendor**: subclass `Collector` in `app/collectors/`, implement `enabled()` and `collect()`,
  register it in `registry.ALL`. Return `UsageEventIn` rows with a stable `dedupe_key`.
- **New unit type**: just send `quantity` + `unit_type` and add a price row with that `unit_type`.
- **Alerts**: `/api/analytics/budgets` returns state per budget; add a scheduled job in `worker.py`
  that posts `warning`/`over` states to Teams/Slack/email.
- **Anomaly detection**: daily z-score per user/department on `usage_daily`, flag > 3σ.
