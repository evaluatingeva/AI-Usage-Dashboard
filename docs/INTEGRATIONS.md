# Enterprise integrations: HR directory, per-user / per-department views, RAG applications

## 1. How a usage event becomes "user X in department Y"

```
AI tool / RAG app ──usage event (user id or tool id)──▶ ingest
                                                    │  look up users (id, email or alias)
HR system ──people feed──▶ users / departments / teams ┘  stamp department_id + team_id on the event
```

1. Every event carries the person who caused it: an email/UPN (SDK, gateway, M365 Copilot, OpenAI),
   or a vendor id (GitHub login, Anthropic `user_...`, Entra object id). Collectors translate vendor
   ids to the person through `users.aliases`.
2. At ingest the person's **department, team and cost centre** come from the directory and are
   stored on the event. Chargeback therefore reflects where the person sat *when they used AI*.
3. Usage that arrives **before** HR knows the person is back-filled when the next directory sync
   runs (last `DIRECTORY_REATTRIBUTE_DAYS`, default 90). After a re-organisation, call
   `POST /api/admin/directory/reattribute?days=90&restate=true` if chargeback should follow the new structure.
4. What each person *sees* is decided by their **role** (set on Governance → People directory, or by
   Entra app roles with OIDC), never by HR data:
   `viewer` = own usage only · `dept_manager` = their department · `finance`/`auditor`/`admin` = firm-wide.

In the dashboard: **Departments & users** → click any department, team, person or engagement for its
own page (spend, tokens, tools, models, apps, daily trend, top users, licensed seats). Parts of spend
with no department are flagged with a hint to connect HR.

## 2. Connecting the HR directory

Pick one source (they can be combined; the last one to run wins per field).

### Workday (Report-as-a-Service)

Ask the Workday HRIS team for:

1. An **Integration System User** (ISU), e.g. `ISU_AI_Usage`, in an integration security group
   with *Get* on *Worker Data: Public Worker Reports*, *Organizations* and *Cost Centers*.
2. An **Advanced custom report** based on *All Active and Terminated Workers* (or *All Active
   Workers*) with these columns, *Enable As Web Service* ticked and shared with the ISU:

   | Column (alias in the report) | Workday field |
   |---|---|
   | `Email_Address` | Primary Work Email |
   | `Worker` | Legal / preferred name |
   | `Employee_ID` | Employee ID |
   | `Supervisory_Organization` | Supervisory organisation, or the service-line level of the org hierarchy |
   | `Department_Code` (optional) | Org reference id / code: keeps department ids stable when names change |
   | `Team` (optional) | Lower org level or sub-department |
   | `Cost_Center` | Cost center |
   | `Active` | Active status (1/0) |
   | `Other_IDs` (optional) | Calculated field with GitHub login etc., `;` separated |

3. The report's JSON URL (Related actions → Web Service → View URLs → JSON).

Then in `.env`:

```env
WORKDAY_REPORT_URL=https://wd5-services1.myworkday.com/ccx/service/customreport2/<tenant>/ISU_AI_Usage/AI_Usage_Directory?format=json
WORKDAY_USERNAME=ISU_AI_Usage@<tenant>
WORKDAY_PASSWORD=...            # from Key Vault / Secrets Manager in production
# Column names differ? map them:
# WORKDAY_FIELD_MAP={"email":"Work_Email","department":"Service_Line"}
```

`docker compose up -d --force-recreate worker api`. The `workday` sync appears on
Governance → Data collectors (runs every `DIRECTORY_SYNC_INTERVAL_MINUTES`, default 12 h; *Run now*
for an immediate sync). OAuth instead of ISU basic auth: set `WORKDAY_BEARER_TOKEN`.

### Entra ID (Microsoft Graph)

Many firms already push department, cost centre and employee id from Workday/SAP into Entra ID.
Add Graph application permission **User.Read.All** (admin consent) to the app registration used
for M365 Copilot and set `ENTRA_DIRECTORY_SYNC=true`. It reads `department`,
`employeeOrgData.costCenter`, `employeeOrgData.division` (team), `employeeId` and stores the Entra
object id as an alias, so Azure APIM events carrying `oid` are attributed too.

### Any other HR system (SAP SuccessFactors, Oracle HCM, ADP, BambooHR…)

Export people as CSV ([template](hr_directory_template.csv)) and upload it on Governance → People
directory → **Import HR CSV**, or automate it:

```bash
curl -X POST "$URL/api/admin/directory/import" -H "Authorization: Bearer $ADMIN_TOKEN" -F file=@people.csv
```

Accepted headers: `email` (or work_email/mail/upn), `name`, `department`, `department_code`,
`team`, `cost_center`, `employee_id`, `aliases` (`;` separated), `active`.

## 3. RAG applications

A RAG application is any app that sends retrieval, vector-search or embedding events with an
`application` name. The **RAG & retrieval** page lists each one with:

* status: **Running** (events in the last `APP_LIVE_MINUTES`, default 60), **Idle** (last 24 h), **Inactive**
* users, retrieval queries, documents per query, embedding tokens, LLM tokens, average retrieval
  latency, error rate, total spend (retrieval + embeddings + the app's LLM calls)
* the vector stores and models it uses, its environment and departments

Click an app for its users, models and daily trend. To make an app appear, instrument it once
(README section 6): set `AIUSAGE_APP="Tax Research Copilot"` and use the SDK's `track_retrieval`,
`track_embedding` and `track_llm`, the LangChain callback, or the LiteLLM gateway callback. Pass the
signed-in user's email as `user_id` so per-user and per-department views include RAG usage.

## 4. Other enterprise systems

| System | What it gives you | How to connect |
|---|---|---|
| Workday / SuccessFactors / Oracle HCM | people → department, team, cost centre | section 2 |
| Entra ID / Okta | sign-in (OIDC), roles via app roles/groups, directory | `AUTH_MODE=oidc` (README §10), section 2 |
| ServiceNow, Salesforce, Workday AI features, other SaaS AI | usage of their built-in AI (Now Assist, Einstein, Workday Assistant…) | most expose usage only as admin reports: schedule the export and send it to `POST /api/ingest/csv` (`event_type=api_call` or `seat`), or forward their usage API through a small collector (copy `app/collectors/openai_collector.py` as a template) |
| SAP Concur / Coupa / procurement | AI subscriptions bought on cards or POs (shadow AI) | export monthly → CSV ingest with `event_type=seat`, `cost_usd` |
| Azure APIM / LiteLLM / Kong AI gateway | per-user tokens for every internal LLM call | README §6 |
| Power BI / Tableau / Snowflake | firm reporting | read the Postgres tables directly (read-only role) or `GET /api/analytics/chargeback.csv` |
| SIEM (Sentinel, Splunk) | security monitoring | ship the API container logs and `audit_log` table |

A new collector is one Python class with `enabled()` and `collect()` returning normalised events;
register it in `app/collectors/registry.py` and it gets scheduling, retries, status on the
Governance page and audit logging automatically.
