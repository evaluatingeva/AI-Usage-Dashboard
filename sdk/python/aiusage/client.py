"""Lightweight usage-reporting client for internal AI applications and RAG pipelines.

Only METERING data leaves the app (token counts, document counts, latency, model,
who/which team). Prompt and completion text are never sent.

    from aiusage import get_client
    usage = get_client()            # reads AIUSAGE_URL / AIUSAGE_API_KEY / AIUSAGE_APP

    usage.track_llm(provider="azure_openai", model="gpt-4o", user_id="jane@firm.com",
                    input_tokens=812, output_tokens=95, latency_ms=1430, project="ENG-1234")

    with usage.track_retrieval(vector_store="azure-ai-search", index_name="tax-kb",
                               user_id="jane@firm.com") as r:
        docs = retriever.invoke(question)
        r.documents_retrieved = len(docs)

Events are buffered and flushed in a background thread (every 5 s or 200 events),
so instrumentation never blocks or breaks the calling application.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aiusage")


@dataclass
class _Span:
    documents_retrieved: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "ok"


class UsageClient:
    def __init__(self, url: str, api_key: str, application: str | None = None, environment: str | None = None,
                 tool_id: str = "internal-apps", flush_interval: float = 5.0, batch_size: int = 200,
                 enabled: bool = True, source: str = "sdk"):
        self.url = url.rstrip("/") + "/api/ingest/events"
        self.api_key, self.application, self.environment = api_key, application, environment
        self.tool_id, self.batch_size, self.enabled, self.source = tool_id, batch_size, enabled, source
        self._q: queue.Queue[dict] = queue.Queue(maxsize=100_000)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, args=(flush_interval,), daemon=True, name="aiusage-flush")
        if enabled:
            self._t.start()
            atexit.register(self.close)

    # ------------------------------------------------------------- public API
    def track(self, **event: Any) -> None:
        if not self.enabled:
            return
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        event.setdefault("tool_id", self.tool_id)
        event.setdefault("application", self.application)
        event.setdefault("environment", self.environment)
        event.setdefault("dedupe_key", f"sdk:{uuid.uuid4()}")
        try:
            self._q.put_nowait({k: v for k, v in event.items() if v is not None})
        except queue.Full:
            log.warning("aiusage buffer full - dropping event")

    def track_llm(self, provider: str, model: str, input_tokens: int, output_tokens: int,
                  cached_input_tokens: int = 0, **kw: Any) -> None:
        self.track(event_type="llm_call", provider=provider, model=model, input_tokens=input_tokens,
                   output_tokens=output_tokens, cached_input_tokens=cached_input_tokens, **kw)

    def track_embedding(self, provider: str, model: str, input_tokens: int, documents: int = 0, **kw: Any) -> None:
        self.track(event_type="embedding", provider=provider, model=model, input_tokens=input_tokens,
                   quantity=documents, unit_type="document" if documents else None, **kw)

    def track_image(self, provider: str, model: str, images: int = 1, **kw: Any) -> None:
        self.track(event_type="image", provider=provider, model=model, quantity=images, unit_type="image", **kw)

    def track_audio(self, provider: str, model: str, minutes: float, **kw: Any) -> None:
        self.track(event_type="audio", provider=provider, model=model, quantity=minutes,
                   unit_type="audio_minute", **kw)

    @contextmanager
    def track_retrieval(self, vector_store: str, index_name: str | None = None, provider: str | None = None, **kw):
        """Time a retrieval / vector query and report documents returned."""
        span, t0 = _Span(), time.perf_counter()
        try:
            yield span
        except Exception:
            span.status = "error"
            raise
        finally:
            self.track(event_type="rag_retrieval", provider=provider or vector_store, vector_store=vector_store,
                       index_name=index_name, documents_retrieved=span.documents_retrieved,
                       latency_ms=round((time.perf_counter() - t0) * 1000, 1), status=span.status,
                       quantity=1, unit_type="query", **kw)

    def flush(self) -> None:
        batch: list[dict] = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
            if len(batch) >= self.batch_size:
                self._send(batch)
                batch = []
        if batch:
            self._send(batch)

    def close(self) -> None:
        self._stop.set()
        self.flush()

    # --------------------------------------------------------------- internals
    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.flush()
            except Exception:  # noqa: BLE001
                log.exception("aiusage flush failed")

    def _send(self, events: list[dict], attempts: int = 3) -> None:
        body = json.dumps({"source": self.source, "events": events}).encode()
        for i in range(attempts):
            try:
                req = urllib.request.Request(self.url, data=body, method="POST", headers={
                    "Content-Type": "application/json", "X-API-Key": self.api_key})
                with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - fixed internal URL
                    if r.status < 300:
                        return
            except Exception as exc:  # noqa: BLE001
                log.warning("aiusage send attempt %d failed: %s", i + 1, exc)
                time.sleep(2 ** i)
        log.error("aiusage dropped %d events after %d attempts", len(events), attempts)


_default: UsageClient | None = None


def get_client() -> UsageClient:
    global _default
    if _default is None:
        _default = UsageClient(
            url=os.environ.get("AIUSAGE_URL", "http://localhost:8000"),
            api_key=os.environ.get("AIUSAGE_API_KEY", ""),
            application=os.environ.get("AIUSAGE_APP"),
            environment=os.environ.get("AIUSAGE_ENV"),
            enabled=os.environ.get("AIUSAGE_DISABLED", "").lower() not in ("1", "true"),
        )
    return _default
