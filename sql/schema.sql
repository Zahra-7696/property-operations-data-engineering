-- Typed source/reference layer.
--
-- Raw files under data/ remain immutable. These tables are typed database copies
-- used by later ingestion, modelling, validation and reporting stages.

CREATE TABLE IF NOT EXISTS src_companies (
    company_id VARCHAR PRIMARY KEY,
    company_name VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS src_contractors (
    contractor_id VARCHAR PRIMARY KEY,
    contractor_name VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS src_properties (
    property_id VARCHAR PRIMARY KEY,
    property_name VARCHAR NOT NULL,
    timezone VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS src_property_management (
    observation_id VARCHAR PRIMARY KEY,
    property_id VARCHAR NOT NULL,
    company_id VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS src_incidents (
    incident_id VARCHAR PRIMARY KEY,
    property_id VARCHAR NOT NULL,
    reported_at TIMESTAMPTZ NOT NULL,
    severity VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS src_report_weeks (
    week_start DATE PRIMARY KEY
);

-- Raw visit-batch landing layer.
--
-- Stage 3 deliberately stores every physical JSONL line before attempting JSON
-- parsing. This preserves malformed input for later technical-validation evidence.

CREATE TABLE IF NOT EXISTS ingestion_batches (
    batch_id VARCHAR PRIMARY KEY,
    manifest_order INTEGER NOT NULL,
    source_file VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    status VARCHAR NOT NULL
        CHECK (status IN ('processing', 'landed', 'completed', 'failed')),
    first_started_at TIMESTAMPTZ NOT NULL,
    last_attempt_at TIMESTAMPTZ NOT NULL,
    landed_at TIMESTAMPTZ,
    validated_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    input_lines INTEGER NOT NULL DEFAULT 0,
    technically_valid_lines INTEGER NOT NULL DEFAULT 0,
    technically_invalid_lines INTEGER NOT NULL DEFAULT 0,
    unique_event_lines INTEGER NOT NULL DEFAULT 0,
    duplicate_event_lines INTEGER NOT NULL DEFAULT 0,
    applied_state_changes INTEGER NOT NULL DEFAULT 0,
    stale_event_lines INTEGER NOT NULL DEFAULT 0,
    business_quality_issues INTEGER NOT NULL DEFAULT 0,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_visit_lines (
    batch_id VARCHAR NOT NULL,
    line_number INTEGER NOT NULL CHECK (line_number > 0),
    raw_payload VARCHAR NOT NULL,
    line_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (batch_id, line_number)
);
