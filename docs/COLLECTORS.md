# Collector set-up, per vendor

Vendors change admin APIs regularly. Each collector names the endpoint it calls; confirm field names
against the vendor's current documentation when you enable it, and run it once manually
(`POST /api/admin/collectors/{name}/run`) and check `GET /api/admin/collectors` for errors.

| Collector | Granularity | Per-user? | Cost source |
|---|---|---|---|
| `openai` | day × model × project × user × API key | Yes (OpenAI user → email) | price catalogue |
| `anthropic` | day × model × workspace × API key; Claude Code per user | API: per key; Claude Code: yes | price catalogue; Claude Code: vendor estimate |
| `azure_openai` | day × deployment × model | No (use APIM or SDK) | price catalogue |
| `bedrock` | day × model id | No (use invocation logs or SDK) | price catalogue |
| `gemini` | day × SKU × GCP project × `team` label | No (labels → team) | billing export (net of credits) |
| `github_copilot` | seat + last activity; org daily metrics | Seats: yes | licence price ÷ days |
| `m365_copilot` | seat + last activity per app | Yes | licence price ÷ days |

## OpenAI

1. platform.openai.com → Organization settings → **Admin keys** → create a key (read-only scope where offered).
2. `OPENAI_ADMIN_KEY=sk-admin-...`
3. Endpoints: `/v1/organization/usage/{completions,embeddings,images,audio_speeches,audio_transcriptions}` and `/v1/organization/users`.
4. Map projects to engagements or departments: give each team its own OpenAI **project**; the project id lands in `project`.
5. ChatGPT Enterprise/Team seats: use the workspace analytics/compliance export and upload via `/api/ingest/csv` with `event_type=seat` rows.

## Anthropic (Claude API, Claude Code)

1. Claude Console → Settings → **Admin keys** (organisation admins only; not available to individual accounts or claude.ai Team plans). The key starts with `sk-ant-admin`. A regular `sk-ant-api...` key is rejected with 401.
2. In `.env`: `ANTHROPIC_ADMIN_KEY=sk-ant-admin01-...` (no quotes, no spaces). Then `docker compose up -d --force-recreate worker api`.
3. Endpoints: the **Usage and Cost Analytics API** (beta header `analytics-usage-cost-2026-08-24`): `GET /v1/organizations/analytics/usage` and `/analytics/cost`, hourly buckets grouped by model, workspace, API key, actor (the user or service account) and product (API vs Claude Code). Cost comes from Anthropic (cents → USD). If the organisation can't use it yet, the collector falls back to the legacy `/v1/organizations/usage_report/messages` and prices from the catalogue.
4. Data lands in Anthropic's analytics within ~4 h (up to 24 h) and is revised for up to ~7 days after month end, so each run re-reads the last 7 days.
5. Per-user attribution: rows carry the actor's email when Anthropic returns it; otherwise add the Anthropic user id (`user_...`) to that person's `users.aliases`. Use one **workspace** per department or programme for team-level attribution.

## Azure OpenAI / Azure AI Foundry

1. Give the worker's managed identity (or a service principal) **Monitoring Reader** on each Azure OpenAI resource.
2. `AZURE_OPENAI_RESOURCE_IDS=/subscriptions/.../resourceGroups/.../providers/Microsoft.CognitiveServices/accounts/<name>,...`
3. Outside Azure set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` for `DefaultAzureCredential`.
4. **Per-user attribution:** put Azure API Management in front of Azure OpenAI, authenticate callers with Entra ID, and add the `llm-emit-token-metric` policy with dimensions `User ID` (from the JWT `oid`/`upn`), `Subscription ID` and `API ID`; or log per-request usage from APIM to Event Hub and forward to `/api/ingest/events`.
5. Cost reconciliation: Azure Cost Management export by resource and meter.

## AWS Bedrock

1. IAM policy for the worker role: `cloudwatch:ListMetrics`, `cloudwatch:GetMetricData` (add more regions by running one worker per region or extending the collector).
2. `BEDROCK_ENABLED=true`, `AWS_REGION=eu-west-1`.
3. **Per-user attribution:** enable Bedrock **model invocation logging** to CloudWatch Logs/S3 (metadata only; disable text delivery so prompts are not stored) and forward `identity.arn`, `modelId`, `input.inputTokenCount`, `output.outputTokenCount` with a Lambda to `/api/ingest/events`. Alternatively use **application inference profiles** per team, tagged with cost-allocation tags.

## Google Gemini / Vertex AI

1. Cloud Billing → **Billing export** → enable *Detailed usage cost* to a BigQuery dataset.
2. Worker identity needs BigQuery Data Viewer on the dataset and BigQuery Job User on the project.
3. `GCP_BILLING_TABLE=myproj.billing.gcp_billing_export_resource_v1_XXXXXX`
4. Enforce a `team` (and `cost_center`) label on every Vertex project via Organization Policy; the collector maps it to `team_id`.
5. Gemini for Google Workspace seats: use the Admin console reports export → CSV ingest.

## GitHub Copilot

1. Fine-grained PAT or GitHub App owned by the org with **GitHub Copilot Business: read** (organisation permission).
2. `GITHUB_ORG=yourfirm`, `GITHUB_TOKEN=...`, `COPILOT_SEAT_PRICE_USD=19` (39 for Enterprise).
3. Endpoints: `/orgs/{org}/copilot/billing/seats` and `/orgs/{org}/copilot/metrics`.
4. Put each person's GitHub login (Enterprise Managed Users: `jdoe_firm`) in `users.aliases`.

## Microsoft 365 Copilot

1. Entra ID → App registrations → new app → API permissions → Microsoft Graph **Reports.Read.All** (application) → admin consent → client secret or certificate (certificate preferred).
2. `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `M365_COPILOT_SEAT_PRICE_USD=30`.
3. Endpoint: `GET /beta/reports/getMicrosoft365CopilotUsageUserDetail(period='D30')`.
4. **Privacy:** if *Display concealed user, group, and site names in all reports* is on (Microsoft 365 admin center → Settings → Org settings → Reports), user names come back obfuscated. Decide with your DPO/Works Council whether to switch it off or keep pseudonymous reporting.

## Everything else (CSV)

`POST /api/ingest/csv?tool_id=<id>&provider=<p>` with a file whose headers match `UsageEventIn`
field names, e.g.

```csv
ts,user_id,model,event_type,input_tokens,output_tokens,quantity,unit_type,cost_usd,project
2026-11-01T00:00:00Z,jane@firm.example,,seat,0,0,1,seat_month,60,
2026-11-01T00:00:00Z,jane@firm.example,midjourney,image,0,0,25,image,,ENG-1033
```

Rows get `dedupe_key = csv:<filename>:<row>`, so re-uploading the same file is safe.
