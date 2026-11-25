"""Collector framework.

A collector PULLS usage from a vendor's admin/billing API on a schedule and turns it
into normalised UsageEventIn records (and, for seat tools, SeatUpdate records).
Collectors must be idempotent: build `dedupe_key` from the vendor's bucket + group
keys so re-runs replace rather than duplicate (ingest is called with upsert=True).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import User
from ..schemas import UsageEventIn


class CollectorError(Exception):
    """An error with a message an operator can act on (shown in the dashboard and audit log)."""

    HINTS = {
        401: "the key was rejected: check it is copied fully, not revoked, and is an ADMIN key",
        403: "the key lacks permission for this report (needs an organization admin key)",
        404: "endpoint not found for this organization or API version",
        429: "rate limited by the vendor: the worker will retry automatically",
    }

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @classmethod
    def from_response(cls, vendor: str, path: str, r: httpx.Response) -> "CollectorError":
        try:
            body = r.json()
            detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
            detail = detail or body.get("message") or str(body)[:300]
        except ValueError:
            detail = r.text[:300]
        hint = cls.HINTS.get(r.status_code, "vendor server error: will retry" if r.status_code >= 500 else "")
        return cls(f"{vendor} {path} returned HTTP {r.status_code}: {detail}" + (f" ({hint})" if hint else ""),
                   r.status_code)


@dataclass
class SeatUpdate:
    tool_id: str
    user_id: str
    last_active_at: datetime | None
    assigned_at: datetime | None = None
    last_surface: str | None = None


@dataclass
class CollectResult:
    events: list[UsageEventIn] = field(default_factory=list)
    seats: list[SeatUpdate] = field(default_factory=list)
    # When set, seat assignments for this tool that are NOT in `seats` are removed
    full_seat_snapshot_for: str | None = None
    # Directory syncs report how many people they processed, plus a one-line summary
    records: int = 0
    note: str | None = None


class Collector(ABC):
    name: str = "base"
    kind: str = "usage"  # usage | directory
    tool_id: str = ""
    provider: str = ""

    def __init__(self, settings: Settings):
        self.settings = settings

    @classmethod
    @abstractmethod
    def enabled(cls, settings: Settings) -> bool: ...

    @abstractmethod
    def collect(self, since: datetime, db: Session) -> CollectResult: ...

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def http() -> httpx.Client:
        # Honour corporate proxies via HTTPS_PROXY / SSL_CERT_FILE env vars
        return httpx.Client(timeout=60, trust_env=True)

    @staticmethod
    def alias_map(db: Session) -> dict[str, str]:
        """alias (github login, vendor user id, email) -> canonical user id"""
        out: dict[str, str] = {}
        for u in db.scalars(select(User)):
            out[u.id.lower()] = u.id
            if u.email:
                out[u.email.lower()] = u.id
            for a in u.aliases or []:
                out[str(a).lower()] = u.id
        return out
