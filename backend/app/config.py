"""Central configuration. Every value can be overridden with an environment variable
(see ../../.env.example). Secrets must come from a secret store (Azure Key Vault,
AWS Secrets Manager, HashiCorp Vault) injected as env vars, never from source control."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Usage Monitor"
    environment: str = "dev"  # dev | staging | prod
    database_url: str = "postgresql+psycopg://aiusage:aiusage@localhost:5432/aiusage"

    # ---- Authentication -------------------------------------------------
    # "dev"  -> local HS256 JWTs issued by /api/auth/dev-login (NEVER in prod)
    # "oidc" -> validate bearer tokens from your IdP (Entra ID / Okta / Ping)
    auth_mode: str = "dev"
    jwt_secret: str = "change-me-dev-only"
    jwt_ttl_minutes: int = 60
    oidc_issuer: str = ""  # e.g. https://login.microsoftonline.com/<tenant>/v2.0
    oidc_audience: str = ""  # API app registration client id / App ID URI
    oidc_jwks_url: str = ""  # e.g. https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys
    oidc_role_claim: str = "roles"  # Entra app roles land in "roles"
    oidc_department_claim: str = "department"

    # ---- Privacy --------------------------------------------------------
    # When true, user identifiers are stored as keyed HMAC pseudonyms; the
    # mapping back to a person is only possible with the key (held by HR/Privacy).
    pseudonymize_users: bool = False
    pseudonymization_key: str = "change-me"
    # Days to keep raw usage events (aggregates are kept longer)
    raw_event_retention_days: int = 400
    # Smallest group size shown in breakdowns to non-admins (k-anonymity)
    min_group_size: int = 1

    # ---- CORS -----------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ---- Collectors (leave blank to disable) ------------------------------
    collector_interval_minutes: int = 60
    collector_lookback_days: int = 3
    openai_admin_key: str = ""
    anthropic_admin_key: str = ""
    azure_subscription_id: str = ""
    azure_openai_resource_ids: str = ""  # comma separated ARM resource ids
    aws_region: str = "us-east-1"
    bedrock_enabled: bool = False
    gcp_billing_table: str = ""  # project.dataset.gcp_billing_export_v1_XXXX
    github_org: str = ""
    github_token: str = ""
    copilot_seat_price_usd: float = 19.0
    ms_graph_tenant_id: str = ""
    ms_graph_client_id: str = ""
    ms_graph_client_secret: str = ""
    m365_copilot_seat_price_usd: float = 30.0

    # ---- HR / identity directory (who is in which department) --------------
    # Workday RaaS custom report (JSON). Basic auth with an Integration System User,
    # or an OAuth bearer token from a Workday API client.
    workday_report_url: str = ""
    workday_username: str = ""
    workday_password: str = ""
    workday_bearer_token: str = ""
    workday_field_map: str = ""  # JSON, e.g. {"email": "Work_Email", "department": "Cost_Center_Hierarchy"}
    # Entra ID users (department, costCenter, employeeId) via Microsoft Graph; reuses MS_GRAPH_* creds
    entra_directory_sync: bool = False
    directory_sync_interval_minutes: int = 720
    directory_reattribute_days: int = 90  # back-fill department on events that arrived before the person was known
    directory_deactivate_missing: bool = False  # mark viewers not in the HR feed as inactive
    # A RAG application counts as "running" if it sent usage within this many minutes
    app_live_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
