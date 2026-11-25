"""RECOMMENDED PATTERN: central AI gateway.

Route every internal LLM call through a LiteLLM proxy (or Azure APIM / Kong AI
Gateway). The gateway knows the caller (virtual key / JWT), the team, the model,
the tokens and the cost of EVERY call regardless of provider - this is the most
reliable way to get per-user, per-engagement attribution.

litellm proxy config.yaml:

    litellm_settings:
      callbacks: ["integrations.litellm_callback.proxy_handler_instance"]

Environment: AIUSAGE_URL, AIUSAGE_API_KEY (ingest key with allowed_sources=gateway)
"""
import os
import sys

from litellm.integrations.custom_logger import CustomLogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from aiusage import UsageClient  # noqa: E402

client = UsageClient(url=os.environ.get("AIUSAGE_URL", "http://api:8000"),
                     api_key=os.environ["AIUSAGE_API_KEY"], application="litellm-gateway",
                     tool_id="ai-gateway", source="gateway")


class AIUsageLogger(CustomLogger):
    def _emit(self, kwargs, response_obj, start_time, end_time, status: str):
        slo = kwargs.get("standard_logging_object") or {}
        meta = slo.get("metadata") or {}
        call_type = slo.get("call_type") or kwargs.get("call_type") or "completion"
        event_type = "embedding" if "embedding" in call_type else "image" if "image" in call_type else "llm_call"
        event = dict(
            event_type=event_type,
            provider=slo.get("custom_llm_provider") or kwargs.get("custom_llm_provider") or "unknown",
            model=slo.get("model") or kwargs.get("model"),
            user_id=meta.get("user_api_key_user_email") or meta.get("user_api_key_user_id") or slo.get("end_user"),
            team_id=meta.get("user_api_key_team_alias") or meta.get("user_api_key_team_id"),
            project=(meta.get("requester_metadata") or {}).get("project"),
            input_tokens=int(slo.get("prompt_tokens") or 0),
            output_tokens=int(slo.get("completion_tokens") or 0),
            cost_usd=float(slo.get("response_cost") or 0) or None,
            latency_ms=round((end_time - start_time).total_seconds() * 1000, 1),
            status=status, dedupe_key=f"litellm:{slo.get('id') or kwargs.get('litellm_call_id')}",
        )
        if event_type == "image":
            event.update(quantity=1, unit_type="image")
        client.track(**event)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._emit(kwargs, response_obj, start_time, end_time, "ok")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._emit(kwargs, response_obj, start_time, end_time, "error")


proxy_handler_instance = AIUsageLogger()
