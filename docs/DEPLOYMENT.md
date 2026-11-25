# Production deployment reference

## Azure (most common for Microsoft-centric firms)

| Concern | Service |
|---|---|
| Containers | Azure Container Apps (simplest) or AKS |
| Database | Azure Database for PostgreSQL Flexible Server (enable the `timescaledb` extension where your region/tier supports it; otherwise use plain Postgres with native partitioning, which works with the code as-is) |
| Secrets | Azure Key Vault + managed identity |
| Identity | Entra ID: one app registration for the API (expose scope `access_as_user`, define app roles), one for the SPA |
| Edge | Azure Front Door + WAF; private endpoints for DB and Key Vault |
| Logs | Azure Monitor / Log Analytics; forward the audit log to Microsoft Sentinel |
| Images | Azure Container Registry with Defender for Containers scanning |

## AWS

ECS Fargate or EKS · Amazon RDS for PostgreSQL (or Timescale Cloud) · Secrets Manager · ALB + AWS WAF ·
CloudWatch / Security Hub · ECR with image scanning. Identity still via your IdP (Entra ID / Okta) over OIDC.

## Production settings

```env
ENVIRONMENT=prod
AUTH_MODE=oidc
OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_AUDIENCE=api://ai-usage-monitor
OIDC_JWKS_URL=https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
JWT_SECRET=<random 64 bytes, unused in oidc mode but must not be the default>
CORS_ORIGINS=https://ai-usage.firm.com
PSEUDONYMIZE_USERS=true            # if the DPIA requires it
MIN_GROUP_SIZE=5
```

## Frontend SSO (MSAL)

Add `@azure/msal-browser` and `@azure/msal-react`, wrap `<App/>` in `MsalProvider`, and replace
`getToken()` in `src/lib/api.ts` with:

```ts
const result = await msal.acquireTokenSilent({ scopes: ["api://ai-usage-monitor/access_as_user"], account });
return result.accessToken;
```

Hide the dev sign-in (the API already returns 404 for `/api/auth/dev-users` when `AUTH_MODE=oidc`).
Users' `department` claim: add it as an optional claim on the API app registration, or let the API
read it from the synced `users` table (already supported).

## Directory sync

Schedule a job (Azure Function / Logic App) that reads Entra ID users with `department`, `jobTitle`,
`employeeOrgData/costCenter` via Graph, or the HRIS feed, and upserts `departments`, `teams`,
`users`. Without it, events still land but show as "unattributed" at department level.

## CI/CD checklist

1. `pytest -q` (backend) and `npm run build` (frontend, type-checks).
2. `pip-audit`, `npm audit --omit=dev`, `bandit -r app`, Semgrep.
3. Build images, scan with Trivy, sign with cosign/Notation, push to registry.
4. Apply `migrations/*.sql` (or adopt Alembic once the schema starts changing).
5. Blue/green or rolling deploy; smoke-test `/api/health`.

## Operations

- **Backups:** PITR 35 days + monthly long-term backup (finance records).
- **DR:** geo-redundant backup; RPO 24h / RTO 8h is usually adequate for a reporting system.
- **Monitoring:** alert on collector runs with `status=error`, ingest 4xx/5xx rate, DB storage.
- **Key rotation:** vendor admin keys every 90 days; ingest keys on staff change or yearly.
