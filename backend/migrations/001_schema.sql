-- Generated from app/models.py for PostgreSQL 15+ / TimescaleDB 2.x
-- Apply with: psql "$DATABASE_URL" -f 001_schema.sql   (or let the API create tables on start-up)

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE ai_tools (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	vendor VARCHAR(100) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	billing_model VARCHAR(32) NOT NULL, 
	approved BOOLEAN NOT NULL, 
	data_classification VARCHAR(32) NOT NULL, 
	owner VARCHAR(200), 
	notes TEXT, 
	PRIMARY KEY (id)
);


CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	actor VARCHAR(200) NOT NULL, 
	action VARCHAR(100) NOT NULL, 
	resource VARCHAR(300), 
	ip VARCHAR(64), 
	details JSON, 
	prev_hash VARCHAR(64) NOT NULL, 
	row_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id)
);


CREATE TABLE budgets (
	id SERIAL NOT NULL, 
	scope_type VARCHAR(16) NOT NULL, 
	scope_id VARCHAR(200) NOT NULL, 
	monthly_limit_usd NUMERIC(14, 2) NOT NULL, 
	alert_threshold_pct INTEGER NOT NULL, 
	owner_email VARCHAR(320), 
	PRIMARY KEY (id)
);


CREATE TABLE collector_runs (
	id SERIAL NOT NULL, 
	collector VARCHAR(64) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	events INTEGER NOT NULL, 
	error TEXT, 
	PRIMARY KEY (id)
);


CREATE TABLE departments (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	cost_center VARCHAR(64), 
	PRIMARY KEY (id)
);


CREATE TABLE ingest_keys (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	key_prefix VARCHAR(12) NOT NULL, 
	key_hash VARCHAR(64) NOT NULL, 
	allowed_sources VARCHAR(200) NOT NULL, 
	created_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key_hash)
);


CREATE TABLE price_catalog (
	id SERIAL NOT NULL, 
	provider VARCHAR(64) NOT NULL, 
	model VARCHAR(200) NOT NULL, 
	unit_type VARCHAR(32) NOT NULL, 
	price_per_unit_usd NUMERIC(18, 10) NOT NULL, 
	effective_from DATE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_price_lookup ON price_catalog (provider, model, unit_type);

CREATE TABLE usage_events (
	id VARCHAR(36) NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	tool_id VARCHAR(64) NOT NULL, 
	provider VARCHAR(64) NOT NULL, 
	model VARCHAR(200), 
	event_type VARCHAR(32) NOT NULL, 
	user_id VARCHAR(200), 
	team_id VARCHAR(64), 
	department_id VARCHAR(64), 
	project VARCHAR(200), 
	application VARCHAR(200), 
	environment VARCHAR(32), 
	input_tokens BIGINT NOT NULL, 
	output_tokens BIGINT NOT NULL, 
	cached_input_tokens BIGINT NOT NULL, 
	requests BIGINT NOT NULL, 
	quantity FLOAT NOT NULL, 
	unit_type VARCHAR(32), 
	documents_retrieved INTEGER NOT NULL, 
	vector_store VARCHAR(200), 
	index_name VARCHAR(200), 
	latency_ms FLOAT, 
	status VARCHAR(16) NOT NULL, 
	cost_usd NUMERIC(18, 8) NOT NULL, 
	cost_source VARCHAR(16) NOT NULL, 
	source VARCHAR(64) NOT NULL, 
	dedupe_key VARCHAR(200) NOT NULL, 
	attributes JSON, 
	ingested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id, ts), 
	CONSTRAINT uq_usage_dedupe UNIQUE (dedupe_key, ts)
);

CREATE INDEX ix_usage_events_event_type ON usage_events (event_type);
CREATE INDEX ix_usage_events_ts ON usage_events (ts);
CREATE INDEX ix_usage_events_team_id ON usage_events (team_id);
CREATE INDEX ix_usage_events_department_id ON usage_events (department_id);
CREATE INDEX ix_usage_events_provider ON usage_events (provider);
CREATE INDEX ix_usage_events_model ON usage_events (model);
CREATE INDEX ix_usage_dept_ts ON usage_events (department_id, ts);
CREATE INDEX ix_usage_events_user_id ON usage_events (user_id);
CREATE INDEX ix_usage_events_tool_id ON usage_events (tool_id);

CREATE TABLE licenses (
	id SERIAL NOT NULL, 
	tool_id VARCHAR(64) NOT NULL, 
	sku VARCHAR(200) NOT NULL, 
	seats_purchased INTEGER NOT NULL, 
	price_per_seat_month_usd NUMERIC(12, 2) NOT NULL, 
	renewal_date DATE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tool_id) REFERENCES ai_tools (id)
);


CREATE TABLE seat_assignments (
	id SERIAL NOT NULL, 
	tool_id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	assigned_at TIMESTAMP WITH TIME ZONE, 
	last_active_at TIMESTAMP WITH TIME ZONE, 
	last_surface VARCHAR(100), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_seat UNIQUE (tool_id, user_id), 
	FOREIGN KEY(tool_id) REFERENCES ai_tools (id)
);

CREATE INDEX ix_seat_assignments_user_id ON seat_assignments (user_id);
CREATE INDEX ix_seat_assignments_tool_id ON seat_assignments (tool_id);

CREATE TABLE teams (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	department_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);


CREATE TABLE users (
	id VARCHAR(200) NOT NULL, 
	display_name VARCHAR(200) NOT NULL, 
	email VARCHAR(320), 
	department_id VARCHAR(64), 
	team_id VARCHAR(64), 
	role VARCHAR(32) NOT NULL, 
	aliases JSON, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);


-- ---------------------------------------------------------------------------
-- TimescaleDB: hypertable, compression, retention and a daily rollup
-- ---------------------------------------------------------------------------
SELECT create_hypertable('usage_events', 'ts', if_not_exists => TRUE, chunk_time_interval => INTERVAL '7 days');

ALTER TABLE usage_events SET (timescaledb.compress, timescaledb.compress_segmentby = 'provider, tool_id');
SELECT add_compression_policy('usage_events', INTERVAL '30 days');

-- Keep raw events ~13 months (data minimisation); keep the daily rollup for 7 years (finance records)
SELECT add_retention_policy('usage_events', INTERVAL '400 days');

CREATE MATERIALIZED VIEW IF NOT EXISTS usage_daily
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS day, tool_id, provider, model, event_type,
       department_id, team_id, project,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       sum(cached_input_tokens) AS cached_input_tokens, sum(requests) AS requests,
       sum(quantity) AS quantity, sum(documents_retrieved) AS documents_retrieved,
       sum(cost_usd) AS cost_usd, count(DISTINCT user_id) AS users
FROM usage_events
GROUP BY day, tool_id, provider, model, event_type, department_id, team_id, project
WITH NO DATA;
SELECT add_continuous_aggregate_policy('usage_daily', start_offset => INTERVAL '7 days',
       end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour');
