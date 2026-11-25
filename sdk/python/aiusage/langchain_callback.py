"""LangChain / LangGraph callback that reports LLM, embedding and retriever usage.

    from aiusage.langchain_callback import AIUsageCallback
    cb = AIUsageCallback(user_id="jane@firm.com", project="ENG-1234")
    chain.invoke({"question": q}, config={"callbacks": [cb]})
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from .client import UsageClient, get_client


class AIUsageCallback(BaseCallbackHandler):
    def __init__(self, client: UsageClient | None = None, provider: str | None = None,
                 vector_store: str | None = None, **dims: Any):
        self.client = client or get_client()
        self.provider, self.vector_store, self.dims = provider, vector_store, dims
        self._starts: dict[UUID, float] = {}

    # ---- LLM -----------------------------------------------------------------
    def on_llm_start(self, serialized, prompts, *, run_id: UUID, **kw):
        self._starts[run_id] = time.perf_counter()

    on_chat_model_start = on_llm_start  # type: ignore[assignment]

    def on_llm_end(self, response, *, run_id: UUID, **kw):
        latency = (time.perf_counter() - self._starts.pop(run_id, time.perf_counter())) * 1000
        out = response.llm_output or {}
        usage = out.get("token_usage") or out.get("usage") or {}
        model = out.get("model_name") or out.get("model")
        # Newer LangChain: usage_metadata on the message
        try:
            meta = response.generations[0][0].message.usage_metadata or {}
            usage = usage or {"prompt_tokens": meta.get("input_tokens"), "completion_tokens": meta.get("output_tokens")}
            model = model or response.generations[0][0].message.response_metadata.get("model_name")
        except (AttributeError, IndexError):
            pass
        self.client.track_llm(
            provider=self.provider or "unknown", model=model or "unknown",
            input_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            latency_ms=round(latency, 1), **self.dims)

    def on_llm_error(self, error, *, run_id: UUID, **kw):
        self._starts.pop(run_id, None)
        self.client.track(event_type="llm_call", provider=self.provider or "unknown", status="error", **self.dims)

    # ---- Retriever (RAG) -------------------------------------------------------
    def on_retriever_start(self, serialized, query, *, run_id: UUID, **kw):
        self._starts[run_id] = time.perf_counter()

    def on_retriever_end(self, documents, *, run_id: UUID, **kw):
        latency = (time.perf_counter() - self._starts.pop(run_id, time.perf_counter())) * 1000
        self.client.track(event_type="rag_retrieval", provider=self.vector_store or "retriever",
                          vector_store=self.vector_store, documents_retrieved=len(documents),
                          latency_ms=round(latency, 1), quantity=1, unit_type="query", **self.dims)
