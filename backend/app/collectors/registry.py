import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select

from ..config import get_settings
from ..db import SessionLocal
from ..models import CollectorRun, SeatAssignment
from ..services.audit import audit
from ..services.ingest import ingest_events
from .anthropic_collector import AnthropicCollector
from .azure_openai_collector import AzureOpenAICollector
from .base import CollectorError, CollectResult, Collector
from .bedrock_collector import BedrockCollector
from .copilot_collectors import GitHubCopilotCollector, M365CopilotCollector
from .gemini_collector import GeminiCollector
from .openai_collector import OpenAICollector
from ..directory.entra import EntraDirectory
from ..directory.workday import WorkdayDirectory

log = logging.getLogger("collectors")

# Directory syncs come first so usage collected in the same cycle is attributed to departments
ALL = [WorkdayDirectory, EntraDirectory, OpenAICollector, AnthropicCollector, AzureOpenAICollector, BedrockCollector, GeminiCollector,
       GitHubCopilotCollector, M365CopilotCollector]


def enabled_collectors() -> list[Collector]:
    s = get_settings()
    return [cls(s) for cls in ALL if cls.enabled(s)]


def _apply_seats(db, result: CollectResult) -> None:
    for s in result.seats:
        row = db.scalar(select(SeatAssignment).where(SeatAssignment.tool_id == s.tool_id,
                                                     SeatAssignment.user_id == s.user_id))
        if row is None:
            row = SeatAssignment(tool_id=s.tool_id, user_id=s.user_id, assigned_at=s.assigned_at)
            db.add(row)
        row.last_active_at = s.last_active_at or row.last_active_at
        row.last_surface = s.last_surface or row.last_surface
    if result.full_seat_snapshot_for:
        current = {s.user_id for s in result.seats}
        db.execute(delete(SeatAssignment).where(SeatAssignment.tool_id == result.full_seat_snapshot_for,
                                                SeatAssignment.user_id.not_in(current)))
    db.commit()


def _describe(exc: Exception) -> str:
    if isinstance(exc, CollectorError):
        return str(exc)
    if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
        return f"network error reaching the vendor API ({exc!r}): check internet access / corporate proxy (HTTPS_PROXY)"
    return f"{type(exc).__name__}: {exc}"


def run_one(collector: Collector) -> int:
    s = get_settings()
    since = (datetime.now(UTC) - timedelta(days=s.collector_lookback_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        run = CollectorRun(collector=collector.name, started_at=datetime.now(UTC), status="running")
        db.add(run)
        db.commit()
        try:
            result = collector.collect(since, db)
            accepted, _ = ingest_events(db, result.events, source=f"collector:{collector.name}", upsert=True)
            _apply_seats(db, result)
            run.status, run.events = "ok", accepted or result.records
            run.error = result.note  # informational summary for directory syncs (status stays "ok")
            log.info("collector %s: %d events, %d seats%s", collector.name, accepted, len(result.seats),
                     f" | {result.note}" if result.note else "")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run.status, run.error = "error", _describe(exc)[:4000]
            log.error("collector %s failed: %s", collector.name, run.error)
        run.finished_at = datetime.now(UTC)
        db.add(run)
        db.commit()
        audit(db, "system:collector", f"collector.{run.status}", collector.name,
              {"events": run.events, **({"error": run.error[:500]} if run.error else {})})
        if run.status == "error":
            raise CollectorError(run.error or "failed")
        return run.events


def run_one_quiet(collector: Collector) -> None:
    """For background tasks: errors are already recorded in collector_runs and the audit log."""
    try:
        run_one(collector)
    except CollectorError:
        pass


def run_all() -> None:
    for c in enabled_collectors():
        try:
            run_one(c)
        except CollectorError:
            pass
