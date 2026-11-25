"""Azure OpenAI / Azure AI Foundry collector (Azure Monitor platform metrics).

Auth: DefaultAzureCredential -> Managed Identity in Azure, or `az login` locally.
Grant the identity "Monitoring Reader" on each Azure OpenAI resource.
Metrics: ProcessedPromptTokens, GeneratedTokens, AzureOpenAIRequests split by
ModelDeploymentName + ModelName (daily totals).

Per-USER attribution is not in platform metrics. For that, front Azure OpenAI with
Azure API Management and the `azure-openai-emit-token-metric` / `llm-emit-token-metric`
policy (dimensions: User ID from the JWT, Subscription ID), or send events from
your apps via the SDK in /sdk/python. See docs/COLLECTORS.md.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectResult, Collector

METRICS = {"ProcessedPromptTokens": "input_tokens", "GeneratedTokens": "output_tokens",
           "AzureOpenAIRequests": "requests"}


class AzureOpenAICollector(Collector):
    name = "azure_openai"
    tool_id = "azure-openai"
    provider = "azure_openai"

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.azure_openai_resource_ids)

    def collect(self, since: datetime, db: Session) -> CollectResult:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import MetricAggregationType, MetricsQueryClient

        client = MetricsQueryClient(DefaultAzureCredential())
        res = CollectResult()
        for resource_id in [r.strip() for r in self.settings.azure_openai_resource_ids.split(",") if r.strip()]:
            resource_name = resource_id.rstrip("/").split("/")[-1]
            agg: dict[tuple, dict] = {}
            result = client.query_resource(
                resource_id, metric_names=list(METRICS), timespan=(since, datetime.now(UTC)),
                granularity=timedelta(days=1), aggregations=[MetricAggregationType.TOTAL],
                filter="ModelDeploymentName eq '*' and ModelName eq '*'",
            )
            for metric in result.metrics:
                field = METRICS[metric.name]
                for series in metric.timeseries:
                    md = {k.lower(): v for k, v in (series.metadata_values or {}).items()}
                    for point in series.data:
                        if not point.total:
                            continue
                        key = (point.timestamp, md.get("modeldeploymentname"), md.get("modelname"))
                        agg.setdefault(key, {"input_tokens": 0, "output_tokens": 0, "requests": 0})
                        agg[key][field] += int(point.total)
            for (ts, deployment, model), v in agg.items():
                res.events.append(UsageEventIn(
                    ts=ts.astimezone(UTC), tool_id=self.tool_id, provider=self.provider, model=model,
                    event_type="llm_call", application=deployment, **v,
                    dedupe_key=f"azure_openai:{resource_name}:{deployment}:{model}",
                    attributes={"resource": resource_name, "deployment": deployment},
                ))
        return res
