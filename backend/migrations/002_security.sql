-- Least-privilege database roles. Run as the DB owner after 001_schema.sql.
-- The API connects as aiusage_app, BI tools as aiusage_readonly.

CREATE ROLE aiusage_app LOGIN PASSWORD :'app_password';
CREATE ROLE aiusage_readonly LOGIN PASSWORD :'ro_password';

GRANT CONNECT ON DATABASE aiusage TO aiusage_app, aiusage_readonly;
GRANT USAGE ON SCHEMA public TO aiusage_app, aiusage_readonly;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aiusage_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO aiusage_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aiusage_readonly;

-- Audit log is append-only for the application
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM aiusage_app;

-- BI / Power BI users never see raw user identifiers
REVOKE SELECT ON users, ingest_keys FROM aiusage_readonly;
