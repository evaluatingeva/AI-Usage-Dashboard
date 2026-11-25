"""Shared ingestion pipeline used by the HTTP API, collectors and CSV import.

normalise -> enrich from directory -> pseudonymise -> cost -> dedupe/upsert -> store
"""
import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import UsageEvent, User
from ..schemas import UsageEventIn
from .pricing import PriceBook

settings = get_settings()


def pseudonymise(user_id: str | None) -> str | None:
    if not user_id or not settings.pseudonymize_users:
        return user_id
    digest = hmac.new(settings.pseudonymization_key.encode(), user_id.lower().encode(), hashlib.sha256)
    return "u_" + digest.hexdigest()[:24]


def ingest_events(db: Session, events: list[UsageEventIn], source: str, upsert: bool = False) -> tuple[int, int]:
    """Store events. upsert=True replaces rows with the same (dedupe_key, ts) - used by
    collectors whose current-day buckets keep growing. Returns (accepted, duplicates)."""
    if not events:
        return 0, 0
    prices = PriceBook(db)
    user_ids = {e.user_id.lower() for e in events if e.user_id}
    directory = {u.id.lower(): u for u in db.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}

    rows: list[UsageEvent] = []
    for e in events:
        ts = e.ts or datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        uid = e.user_id.lower() if e.user_id else None
        person = directory.get(uid) if uid else None
        row = UsageEvent(
            id=str(uuid.uuid4()), ts=ts, tool_id=e.tool_id, provider=e.provider, model=e.model,
            event_type=e.event_type, user_id=pseudonymise(uid),
            team_id=e.team_id or (person.team_id if person else None),
            department_id=e.department_id or (person.department_id if person else None),
            project=e.project, application=e.application, environment=e.environment,
            input_tokens=e.input_tokens, output_tokens=e.output_tokens,
            cached_input_tokens=e.cached_input_tokens, requests=e.requests,
            quantity=e.quantity, unit_type=e.unit_type, documents_retrieved=e.documents_retrieved,
            vector_store=e.vector_store, index_name=e.index_name, latency_ms=e.latency_ms,
            status=e.status, source=source, attributes=e.attributes,
            dedupe_key=e.dedupe_key or f"{source}:{uuid.uuid4()}",
        )
        if e.cost_usd is not None:
            row.cost_usd, row.cost_source = e.cost_usd, "provider"
        else:
            c = prices.cost(row)
            row.cost_usd, row.cost_source = (c, "computed") if c is not None else (0, "none")
        rows.append(row)

    # Drop duplicates inside the batch itself (last one wins)
    unique: dict[tuple, UsageEvent] = {}
    for r in rows:
        unique[(r.dedupe_key, r.ts)] = r
    batch_dupes = len(rows) - len(unique)
    rows = list(unique.values())

    wanted = {(r.dedupe_key, _utc(r.ts)) for r in rows}
    key_list = sorted({r.dedupe_key for r in rows})
    existing: set[tuple] = set()
    for i in range(0, len(key_list), 500):
        chunk = key_list[i:i + 500]
        for k, t in db.execute(select(UsageEvent.dedupe_key, UsageEvent.ts).where(UsageEvent.dedupe_key.in_(chunk))):
            if (k, _utc(t)) in wanted:
                existing.add((k, _utc(t)))
    duplicates = batch_dupes
    if upsert:
        for k, t in existing:
            db.execute(delete(UsageEvent).where(UsageEvent.dedupe_key == k, UsageEvent.ts == t))
    else:
        fresh = [r for r in rows if (r.dedupe_key, _utc(r.ts)) not in existing]
        duplicates += len(rows) - len(fresh)
        rows = fresh
    cols = [c.key for c in UsageEvent.__table__.columns if c.key != "ingested_at"]
    payload = [{c: getattr(r, c) for c in cols} for r in rows]
    for i in range(0, len(payload), 1000):  # bulk Core insert: fast and dialect-neutral
        db.execute(insert(UsageEvent), payload[i:i + 1000])
    db.commit()
    return len(rows), duplicates


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)
