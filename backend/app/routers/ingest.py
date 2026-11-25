import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..auth import Principal, ingest_key_auth, require
from ..db import get_db
from ..models import IngestKey
from ..schemas import IngestBatch, IngestResult, UsageEventIn
from ..services.audit import audit
from ..services.ingest import ingest_events

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/events", response_model=IngestResult)
def ingest(batch: IngestBatch, key: IngestKey = Depends(ingest_key_auth), db: Session = Depends(get_db)):
    """Machine-to-machine endpoint for SDKs, AI gateways (LiteLLM, APIM, Kong) and
    RAG pipelines. Authenticate with an ingest key: header `X-API-Key`."""
    if key.allowed_sources != "*" and batch.source not in key.allowed_sources.split(","):
        raise HTTPException(403, f"key '{key.name}' may not ingest source '{batch.source}'")
    accepted, dupes = ingest_events(db, batch.events, source=batch.source)
    return IngestResult(accepted=accepted, duplicates=dupes)


@router.post("/csv", response_model=IngestResult)
async def ingest_csv(file: UploadFile, request: Request, tool_id: str, provider: str,
                     p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """Bulk upload for tools without an API (vendor invoices, ChatGPT Enterprise
    exports...). Columns match UsageEventIn field names; unknown columns are ignored."""
    text = (await file.read()).decode("utf-8-sig")
    events = []
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        row = {k: v for k, v in row.items() if v not in (None, "")}
        row.setdefault("tool_id", tool_id)
        row.setdefault("provider", provider)
        row.setdefault("dedupe_key", f"csv:{file.filename}:{i}")
        try:
            events.append(UsageEventIn.model_validate(row))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"row {i}: {exc}") from exc
    accepted, dupes = ingest_events(db, events, source="csv")
    audit(db, p.id, "ingest.csv", file.filename, {"rows": len(events), "accepted": accepted},
          ip=request.client.host if request.client else None)
    return IngestResult(accepted=accepted, duplicates=dupes)
