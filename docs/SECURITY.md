# Security, privacy and compliance

This system processes **employee activity data** and **engagement/client codes**. Treat it as
Confidential. The controls below are built in or are configuration steps; the "Owner" column says who
signs off in a typical professional-services firm.

## Control summary

| Area | Control | Where | Owner |
|---|---|---|---|
| Data minimisation | Only metering metadata; content keys rejected at ingest | `schemas.py` `FORBIDDEN_ATTRIBUTE_KEYS` | DPO |
| Data minimisation | Raw events auto-deleted after `RAW_EVENT_RETENTION_DAYS` (default 400); daily rollups kept for finance retention | `worker.retention`, `001_schema.sql` | DPO / Finance |
| Pseudonymisation | Keyed HMAC of user ids, key held outside the platform team | `PSEUDONYMIZE_USERS`, `PSEUDONYMIZATION_KEY` | DPO |
| Aggregation threshold | Groups smaller than `MIN_GROUP_SIZE` hidden from non-org-wide roles | `analytics.breakdown` | DPO |
| Authentication | OIDC (Entra ID / Okta), RS256 signature, issuer and audience checked; MFA + Conditional Access enforced at the IdP | `auth.py` | IAM |
| Authorisation | 5 roles, permission matrix, row-level scoping on every analytics query | `auth.PERMISSIONS`, `usage_scope()` | IAM / data owner |
| Machine access | Per-application ingest keys, stored as SHA-256 only, revocable, source-restricted | `IngestKey` | Platform |
| Audit | Append-only, SHA-256 hash-chained log of logins, exports, config changes, collector runs; verify endpoint | `services/audit.py` | Internal Audit |
| Audit | DB role cannot UPDATE/DELETE `audit_log` | `002_security.sql` | DBA |
| Secrets | Vendor admin keys from Key Vault / Secrets Manager via managed identity; nothing in git | `.env.example`, deployment | Platform |
| Transport | TLS 1.2+ end to end; HSTS; private endpoints to DB | nginx / ingress | Network |
| At rest | Managed-DB encryption (customer-managed keys if policy requires) | Cloud | Platform |
| App hardening | Security headers, CSP, no OpenAPI in prod, non-root containers, input validation, parameterised SQL (ORM) | `main.py`, `nginx.conf`, Dockerfiles | AppSec |
| Supply chain | Pinned dependencies; scan with pip-audit / npm audit / Trivy in CI | CI | AppSec |

## Role matrix

| Permission | admin | finance | auditor | dept_manager | viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| Analytics (scope) | firm | firm | firm | own department | own usage |
| Licences / budgets: read | ✓ | ✓ | ✓ | ✓ (own dept) | |
| Licences / budgets: write | ✓ | ✓ | | | |
| Chargeback export | ✓ | ✓ | ✓ | | |
| Audit log | ✓ | | ✓ | | |
| Tools, prices, ingest keys, collectors | ✓ | | | | |

Map roles to Entra ID **app roles** on the API app registration and assign them to security groups
(e.g. `SG-AIUsage-Finance`). Review membership quarterly.

## Privacy (GDPR / UK GDPR / local law): inputs for the DPIA

- **Purpose:** cost management, licence optimisation, AI governance and risk oversight. *Not*
  individual performance evaluation: state this explicitly in the employee notice and enforce it
  through the role matrix (line managers below department level get no per-user view by default).
- **Lawful basis:** legitimate interests (cost control, security, regulatory obligations around AI
  use); document the balancing test.
- **Data categories:** work email/UPN, department, team, timestamps, tool and model used, volumes,
  cost, engagement code. No special-category data; no content.
- **Transparency:** update the employee privacy notice and AI acceptable-use policy.
- **Works councils / employee representatives** (e.g. Germany, Netherlands, France): employee
  monitoring tools usually require consultation or agreement before go-live.
- **Cross-border transfers:** host in the region of the member firm; vendor admin APIs (US) return
  usage about your users, so record them as processors/sub-processors.
- **Data subject rights:** access requests can be served from `/api/analytics/*` as the user;
  erasure via deleting their `users` row and pseudonymising historic events.

## Professional-services specifics

- **Client confidentiality:** engagement codes and application names can reveal client
  relationships. Restrict `project`-level views to finance/risk roles if your policy requires it.
- **Independence:** if usage data is used to bill AI costs to audit clients, have Risk/Independence
  review the chargeback method.
- **Assurance-ready:** the hash-chained audit trail, role matrix and collector run log support SOC 2
  / ISO 27001 evidence requests and internal-audit testing of the platform.
- **Shadow AI:** tools found through CASB/proxy logs (Netskope, Zscaler, Defender for Cloud Apps)
  but not approved can be registered with `approved=false` so they appear in the inventory and risk
  register.

## Threat-model highlights

| Threat | Mitigation |
|---|---|
| Stolen ingest key used to inject false usage | Per-app keys, `allowed_sources`, ingestion only from internal networks, anomaly checks, revoke from Governance |
| Privilege escalation via token tampering | Signature + issuer + audience validation; role only from IdP claims |
| Insider reads colleagues' usage | Row scoping, audit of exports, pseudonymisation, aggregation threshold |
| Audit tampering | Hash chain + verify endpoint + DB grants + SIEM copy |
| Vendor admin key leak | Read-only admin keys where vendors support it, vault storage, rotation every 90 days |
| Prompt data leakage | Content keys rejected; never log request bodies at the gateway callback |
