"""AWS Bedrock collector (CloudWatch AWS/Bedrock namespace).

Auth: standard boto3 chain (IAM role / IRSA / SSO profile). IAM needs
cloudwatch:ListMetrics and cloudwatch:GetMetricData.
Metrics per ModelId: InputTokenCount, OutputTokenCount, Invocations (daily Sum).

Per-USER attribution: enable Bedrock *model invocation logging* to S3/CloudWatch Logs
(it records identity.arn per call) and ship those logs to /api/ingest/events, or
use application inference profiles tagged per team. See docs/COLLECTORS.md.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectResult, Collector

METRICS = {"InputTokenCount": "input_tokens", "OutputTokenCount": "output_tokens", "Invocations": "requests"}


class BedrockCollector(Collector):
    name = "bedrock"
    tool_id = "aws-bedrock"
    provider = "bedrock"

    @classmethod
    def enabled(cls, s) -> bool:
        return s.bedrock_enabled

    def collect(self, since: datetime, db: Session) -> CollectResult:
        import boto3

        cw = boto3.client("cloudwatch", region_name=self.settings.aws_region)
        model_ids = sorted({d["Value"] for page in cw.get_paginator("list_metrics").paginate(
            Namespace="AWS/Bedrock", MetricName="Invocations")
            for m in page["Metrics"] for d in m["Dimensions"] if d["Name"] == "ModelId"})
        queries, index = [], {}
        for i, model_id in enumerate(model_ids):
            for metric, field in METRICS.items():
                qid = f"m{i}_{field}"
                index[qid] = (model_id, field)
                queries.append({"Id": qid, "MetricStat": {"Metric": {
                    "Namespace": "AWS/Bedrock", "MetricName": metric,
                    "Dimensions": [{"Name": "ModelId", "Value": model_id}]}, "Period": 86400, "Stat": "Sum"}})
        agg: dict[tuple, dict] = {}
        for start in range(0, len(queries), 500):  # API limit: 500 queries per call
            for page in cw.get_paginator("get_metric_data").paginate(
                    MetricDataQueries=queries[start:start + 500], StartTime=since, EndTime=datetime.now(UTC)):
                for r in page["MetricDataResults"]:
                    model_id, field = index[r["Id"]]
                    for ts, val in zip(r["Timestamps"], r["Values"], strict=False):
                        key = (ts.astimezone(UTC), model_id)
                        agg.setdefault(key, {"input_tokens": 0, "output_tokens": 0, "requests": 0})
                        agg[key][field] += int(val)
        res = CollectResult()
        for (ts, model_id), v in agg.items():
            res.events.append(UsageEventIn(
                ts=ts, tool_id=self.tool_id, provider=self.provider, model=model_id, event_type="llm_call",
                **v, dedupe_key=f"bedrock:{self.settings.aws_region}:{model_id}",
                attributes={"region": self.settings.aws_region},
            ))
        return res
