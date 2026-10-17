-- Stage 2: typed source/reference layer.
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
