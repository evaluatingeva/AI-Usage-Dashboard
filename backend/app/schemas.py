from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "llm_call", "embedding", "rag_retrieval", "vector_query", "rerank",
    "image", "audio", "api_call", "seat", "tool_call",
]

FORBIDDEN_ATTRIBUTE_KEYS = {"prompt", "completion", "messages", "input", "output", "content", "text", "query_text"}


class UsageEventIn(BaseModel):
    """One unit of AI consumption. Send prompt/response CONTENT nowhere near this API."""

    ts: datetime | None = None
    tool_id: str = Field(..., max_length=64)
    provider: str = Field(..., max_length=64)
    model: str | None = None
    event_type: EventType = "llm_call"
    user_id: str | None = None
    team_id: str | None = None
    department_id: str | None = None
    project: str | None = None
    application: str | None = None
    environment: str | None = None
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    cached_input_tokens: int = Field(0, ge=0)
    requests: int = Field(1, ge=0)
    quantity: float = Field(0, ge=0)
    unit_type: str | None = None
    documents_retrieved: int = Field(0, ge=0)
    vector_store: str | None = None
    index_name: str | None = None
    latency_ms: float | None = None
    status: Literal["ok", "error", "blocked"] = "ok"
    cost_usd: float | None = Field(None, ge=0)
    dedupe_key: str | None = Field(None, max_length=200)
    attributes: dict[str, Any] | None = None

    @field_validator("attributes")
    @classmethod
    def no_content(cls, v):
        if v and FORBIDDEN_ATTRIBUTE_KEYS & {k.lower() for k in v}:
            raise ValueError(
                "attributes must not contain prompt/response content "
                f"(forbidden keys: {sorted(FORBIDDEN_ATTRIBUTE_KEYS)})"
            )
        return v


class IngestBatch(BaseModel):
    source: str = Field("sdk", max_length=64)
    events: list[UsageEventIn] = Field(..., max_length=5000)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int


class LoginIn(BaseModel):
    user_id: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class BudgetIn(BaseModel):
    scope_type: Literal["org", "department", "team", "tool", "project"]
    scope_id: str
    monthly_limit_usd: float = Field(..., gt=0)
    alert_threshold_pct: int = Field(80, ge=1, le=100)
    owner_email: str | None = None


class LicenseIn(BaseModel):
    tool_id: str
    sku: str
    seats_purchased: int = Field(..., ge=0)
    price_per_seat_month_usd: float = Field(..., ge=0)
    renewal_date: date | None = None


class PriceIn(BaseModel):
    provider: str
    model: str
    unit_type: str
    price_per_unit_usd: float = Field(..., ge=0)
    effective_from: date = date(2024, 1, 1)


class ToolIn(BaseModel):
    id: str
    name: str
    vendor: str
    category: str
    billing_model: str
    approved: bool = True
    data_classification: str = "internal"
    owner: str | None = None
    notes: str | None = None


class IngestKeyIn(BaseModel):
    name: str
    allowed_sources: str = "*"


class UserUpdate(BaseModel):
    role: str | None = None
    department_id: str | None = None
    team_id: str | None = None
    aliases: list[str] | None = None
    active: bool | None = None
