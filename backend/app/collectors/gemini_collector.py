"""Google Gemini / Vertex AI collector (Cloud Billing export to BigQuery).

Prerequisite: Billing -> Billing export -> "Detailed usage cost" to BigQuery.
Auth: Application Default Credentials / Workload Identity with BigQuery Data Viewer
+ Job User on the billing dataset. Set GCP_BILLING_TABLE=project.dataset.table.

Cost comes straight from billing (net of credits). Token counts are derived from
the SKU description ("... Input ... tokens"/"characters"). Team attribution comes
from GCP project or resource labels (`team`, `cost_center`) - enforce labels via
Org Policy so every Vertex project is tagged.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..schemas import UsageEventIn
from .base import CollectResult, Collector

SQL = """
SELECT DATE(usage_start_time) AS day,
       service.description AS service, sku.description AS sku, project.id AS project_id,
       (SELECT value FROM UNNEST(project.labels) WHERE key = 'team') AS team,
       usage.unit AS unit,
       SUM(usage.amount) AS qty,
       SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost
FROM `{table}`
WHERE usage_start_time >= @since
  AND (service.description IN ('Vertex AI', 'Gemini API', 'Generative Language API')
       OR LOWER(sku.description) LIKE '%gemini%')
GROUP BY day, service, sku, project_id, team, unit
"""


class GeminiCollector(Collector):
    name = "gemini"
    tool_id = "google-gemini"
    provider = "google"

    @classmethod
    def enabled(cls, s) -> bool:
        return bool(s.gcp_billing_table)

    def collect(self, since: datetime, db: Session) -> CollectResult:
        from google.cloud import bigquery

        bq = bigquery.Client()
        job = bq.query(SQL.format(table=self.settings.gcp_billing_table), job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)]))
        res = CollectResult()
        for r in job.result():
            sku = (r["sku"] or "").lower()
            qty = float(r["qty"] or 0)
            tokens_in = qty if ("input" in sku and ("token" in sku or "character" in sku)) else 0
            tokens_out = qty if ("output" in sku and ("token" in sku or "character" in sku)) else 0
            other = not (tokens_in or tokens_out)
            model = next((w for w in sku.replace(",", " ").split() if w.startswith("gemini")), None)
            res.events.append(UsageEventIn(
                ts=datetime.combine(r["day"], datetime.min.time(), UTC), tool_id=self.tool_id,
                provider=self.provider, model=model or r["sku"], event_type="llm_call" if not other else "api_call",
                team_id=r["team"], project=r["project_id"], input_tokens=int(tokens_in),
                output_tokens=int(tokens_out), requests=0, quantity=qty if other else 0,
                unit_type=(r["unit"] if other else None), cost_usd=max(float(r["net_cost"] or 0), 0),
                dedupe_key=f"gcp:{r['project_id']}:{r['sku']}:{r['team']}",
                attributes={"service": r["service"], "sku": r["sku"]},
            ))
        return res
