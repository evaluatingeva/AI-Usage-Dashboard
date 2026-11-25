"""Database schema.

Design principles
- ONE normalised fact table (usage_events) for every kind of AI consumption:
  tokens, embeddings, RAG retrievals, vector queries, images, audio minutes,
  API calls, seat-days. `event_type` + `unit_type` say what `quantity` means.
- Dimensions (who / what / where) are denormalised onto the event so analytics
  stay fast on a TimescaleDB hypertable.
- NO prompt or completion content is ever stored - only metering metadata.
- Every event carries a `dedupe_key` so collectors can re-run safely.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- org
class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "audit"
    name: Mapped[str] = mapped_column(String(200))
    cost_center: Mapped[str | None] = mapped_column(String(64))


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))


class User(Base):
    """Directory mirror (sync from Entra ID / HRIS). `id` is the canonical user key
    used on events: usually the UPN/email, or its pseudonym when enabled."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    # Other identities of the same person in AI tools: GitHub login, OpenAI user id...
    aliases: Mapped[list | None] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ------------------------------------------------------------------------- tools
class AITool(Base):
    """Catalogue of AI tools / providers in use (the 'AI inventory')."""

    __tablename__ = "ai_tools"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # "openai", "github-copilot"
    name: Mapped[str] = mapped_column(String(200))
    vendor: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(32))  # llm_api | assistant_seat | rag | vector_db | image | audio | other
    billing_model: Mapped[str] = mapped_column(String(32))  # usage | seat | hybrid
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    data_classification: Mapped[str] = mapped_column(String(32), default="internal")
    owner: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class PriceCatalog(Base):
    """Unit prices used to cost events that arrive without a cost.
    Keep this in sync with your negotiated enterprise rates (not list price)."""

    __tablename__ = "price_catalog"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))  # exact model, or prefix ending in '*'
    unit_type: Mapped[str] = mapped_column(String(32))  # input_tokens | output_tokens | cached_input_tokens | image | audio_minute | request | query | seat_month
    price_per_unit_usd: Mapped[float] = mapped_column(Numeric(18, 10))
    effective_from: Mapped[date] = mapped_column(Date, default=date(2024, 1, 1))
    __table_args__ = (Index("ix_price_lookup", "provider", "model", "unit_type"),)


# ------------------------------------------------------------------------ facts
class UsageEvent(Base):
    __tablename__ = "usage_events"
    # Composite PK (id, ts) so the table can become a TimescaleDB hypertable.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)

    # what
    tool_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(200), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    # llm_call | embedding | rag_retrieval | vector_query | rerank | image | audio | api_call | seat | tool_call

    # who / where
    user_id: Mapped[str | None] = mapped_column(String(200), index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64), index=True)
    project: Mapped[str | None] = mapped_column(String(200))  # engagement / client code
    application: Mapped[str | None] = mapped_column(String(200))  # internal app that made the call
    environment: Mapped[str | None] = mapped_column(String(32))

    # how much
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    requests: Mapped[int] = mapped_column(BigInteger, default=1)
    quantity: Mapped[float] = mapped_column(Float, default=0)  # non-token units
    unit_type: Mapped[str | None] = mapped_column(String(32))  # image | audio_minute | query | document | seat_day ...

    # RAG specifics
    documents_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    vector_store: Mapped[str | None] = mapped_column(String(200))
    index_name: Mapped[str | None] = mapped_column(String(200))

    latency_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | blocked
    cost_usd: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    cost_source: Mapped[str] = mapped_column(String(16), default="computed")  # provider | computed | none

    source: Mapped[str] = mapped_column(String(64))  # collector:openai | sdk | gateway | csv
    dedupe_key: Mapped[str] = mapped_column(String(200))
    attributes: Mapped[dict | None] = mapped_column(JSON)  # free-form, NEVER prompt content
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dedupe_key", "ts", name="uq_usage_dedupe"),
        Index("ix_usage_dept_ts", "department_id", "ts"),
    )


# -------------------------------------------------------------------- licensing
class License(Base):
    __tablename__ = "licenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey("ai_tools.id"))
    sku: Mapped[str] = mapped_column(String(200))
    seats_purchased: Mapped[int] = mapped_column(Integer)
    price_per_seat_month_usd: Mapped[float] = mapped_column(Numeric(12, 2))
    renewal_date: Mapped[date | None] = mapped_column(Date)


class SeatAssignment(Base):
    __tablename__ = "seat_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey("ai_tools.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_surface: Mapped[str | None] = mapped_column(String(100))  # vscode, teams, word...
    __table_args__ = (UniqueConstraint("tool_id", "user_id", name="uq_seat"),)


# ---------------------------------------------------------------------- budgets
class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16))  # org | department | team | tool | project
    scope_id: Mapped[str] = mapped_column(String(200))
    monthly_limit_usd: Mapped[float] = mapped_column(Numeric(14, 2))
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, default=80)
    owner_email: Mapped[str | None] = mapped_column(String(320))


# ------------------------------------------------------------- security & audit
class IngestKey(Base):
    """API keys for machine ingestion (SDKs, gateways). Only the SHA-256 hash is stored."""

    __tablename__ = "ingest_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    allowed_sources: Mapped[str] = mapped_column(String(200), default="*")
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Append-only, hash-chained audit trail. Each row's hash covers the previous
    row's hash, so any tampering breaks the chain (verify with /api/admin/audit/verify).
    In production also stream to your SIEM (Splunk / Sentinel) and revoke UPDATE/DELETE
    on this table from the application role."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))


class CollectorRun(Base):
    __tablename__ = "collector_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    events: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
