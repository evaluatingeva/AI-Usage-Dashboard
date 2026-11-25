"""Scheduler process: runs each enabled collector on its own schedule, retries soon
after a failure, and purges old raw events nightly. Start with:  python -m app.worker"""
import logging
import time
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, text

from .collectors.base import CollectorError
from .collectors.registry import enabled_collectors, run_one
from .config import get_settings
from .db import SessionLocal, engine, init_db
from .models import UsageEvent
from .services.audit import audit

log = logging.getLogger("worker")
RETRY_MINUTES = [5, 15, 30]  # after the 3rd consecutive failure, fall back to the normal interval
_failures: dict[str, int] = {}


def retention() -> None:
    """Delete raw events older than RAW_EVENT_RETENTION_DAYS (data minimisation).
    With TimescaleDB prefer: SELECT add_retention_policy('usage_events', INTERVAL '400 days');
    and keep a continuous aggregate for long-term trend reporting."""
    s = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=s.raw_event_retention_days)
    with SessionLocal() as db:
        n = db.execute(delete(UsageEvent).where(UsageEvent.ts < cutoff)).rowcount
        db.commit()
        audit(db, "system:retention", "retention.purge", "usage_events", {"deleted": n, "cutoff": cutoff.isoformat()})


def make_job(sched: BlockingScheduler, collector):
    def job():
        try:
            run_one(collector)
            _failures[collector.name] = 0
        except CollectorError as exc:
            n = _failures.get(collector.name, 0)
            _failures[collector.name] = n + 1
            if n < len(RETRY_MINUTES):
                at = datetime.now(UTC) + timedelta(minutes=RETRY_MINUTES[n])
                log.warning("collector %s failed (%s); retrying at %s UTC", collector.name, exc, at.strftime("%H:%M"))
                sched.add_job(job, "date", run_date=at, id=f"retry-{collector.name}", replace_existing=True)
    return job


def wait_for_db(timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                raise
            log.info("waiting for database: %s", exc)
            time.sleep(3)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    wait_for_db()
    init_db()
    collectors = enabled_collectors()
    log.info("enabled collectors: %s", [c.name for c in collectors] or "none")
    sched = BlockingScheduler(timezone="UTC")
    first = datetime.now(UTC) + timedelta(seconds=30)  # give the API a moment to finish start-up
    for i, c in enumerate(collectors):
        minutes = s.directory_sync_interval_minutes if c.kind == "directory" else s.collector_interval_minutes
        sched.add_job(make_job(sched, c), "interval", minutes=minutes, id=c.name,
                      next_run_time=first + timedelta(seconds=10 * i), max_instances=1, coalesce=True)
    sched.add_job(retention, "cron", hour=2, minute=30)
    sched.start()


if __name__ == "__main__":
    main()
