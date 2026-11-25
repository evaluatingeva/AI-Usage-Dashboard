import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog

GENESIS = "0" * 64


def _digest(prev_hash: str, ts: datetime, actor: str, action: str, resource: str | None, details: dict | None) -> str:
    body = json.dumps(
        {"prev": prev_hash, "ts": ts.isoformat(), "actor": actor, "action": action,
         "resource": resource, "details": details},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def audit(db: Session, actor: str, action: str, resource: str | None = None,
          details: dict | None = None, ip: str | None = None, commit: bool = True) -> AuditLog:
    """Append a hash-chained audit record. Postgres: run inside a serialisable
    transaction or a single writer to keep the chain linear (see docs/SECURITY.md)."""
    last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    prev = last.row_hash if last else GENESIS
    ts = datetime.now(UTC).replace(microsecond=0)
    row = AuditLog(ts=ts, actor=actor, action=action, resource=resource, ip=ip, details=details,
                   prev_hash=prev, row_hash=_digest(prev, ts, actor, action, resource, details))
    db.add(row)
    if commit:
        db.commit()
    return row


def verify_chain(db: Session) -> dict:
    prev = GENESIS
    count = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=UTC)
        expected = _digest(prev, ts, row.actor, row.action, row.resource, row.details)
        if row.prev_hash != prev or row.row_hash != expected:
            return {"valid": False, "broken_at_id": row.id, "checked": count}
        prev = row.row_hash
        count += 1
    return {"valid": True, "checked": count}
