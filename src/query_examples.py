from __future__ import annotations

from collections.abc import Sequence

from src.database import connect_database


# =============================================================================
# DUCKDB QUERY EXAMPLES
# =============================================================================
#
# Purpose:
#   Read-only inspection helper for reviewing the DuckDB data model stage by stage.
#
# How to use:
#   1. Find the RUN section near the bottom of this file.
#   2. Uncomment ONE OR MORE RUN.append(...) lines.
#   3. From the repository root run:
#
#          python -m src.query_examples
#
#   4. The script prints:
#        - what the query is intended to return;
#        - the SQL itself;
#        - the query result;
#        - the number of returned rows.
#
# All queries use connect_database(read_only=True), so this helper does not modify
# the DuckDB database.
#
# NOTE:
#   Stage 1 creates no DuckDB table. It verifies the supplied source files.
#   Stage 11 adds CI/reproducibility files, not business-data tables.
#   Stage 12 adds production-design documentation, not business-data tables.
# =============================================================================


# =============================================================================
# GENERAL / SCHEMA
# =============================================================================
#
# These queries help explain the overall database before inspecting a stage.
#
# SHOW_ALL_TABLES
#   Returns all current DuckDB tables/views.
#
# DESCRIBE_VISIT_CURRENT_STATE
#   Returns the column names, DuckDB data types, nullability and key information
#   for visit_current_state.
#
# DESCRIBE_FACT_VISITS
#   Returns the column names, DuckDB data types, nullability and key information
#   for fact_visits.
#
# PREVIEW_VISIT_CURRENT_STATE / PREVIEW_FACT_VISITS
#   Return the first 10 rows so a reviewer can see actual table contents.
# =============================================================================

SHOW_ALL_TABLES = """
SHOW TABLES;
"""

DESCRIBE_VISIT_CURRENT_STATE = """
DESCRIBE visit_current_state;
"""

DESCRIBE_FACT_VISITS = """
DESCRIBE fact_visits;
"""

PREVIEW_VISIT_CURRENT_STATE = """
SELECT *
FROM visit_current_state
LIMIT 10;
"""

PREVIEW_FACT_VISITS = """
SELECT *
FROM fact_visits
LIMIT 10;
"""


# =============================================================================
# STAGE 2 — TYPED REFERENCE DATA
# =============================================================================
#
# Aim:
#   Load the six CSV reference/source files into typed DuckDB tables.
#
# Tables created:
#   1. src_companies
#   2. src_contractors
#   3. src_properties
#   4. src_property_management
#   5. src_incidents
#   6. src_report_weeks

# Creation SQL (from sql/schema.sql)
#
# 1. src_companies
#
# CREATE TABLE IF NOT EXISTS src_companies (
#     company_id VARCHAR PRIMARY KEY,
#     company_name VARCHAR NOT NULL
# );
#
# 2. src_contractors
#
# CREATE TABLE IF NOT EXISTS src_contractors (
#     contractor_id VARCHAR PRIMARY KEY,
#     contractor_name VARCHAR NOT NULL
# );
#
# 3. src_properties
#
# CREATE TABLE IF NOT EXISTS src_properties (
#     property_id VARCHAR PRIMARY KEY,
#     property_name VARCHAR NOT NULL,
#     timezone VARCHAR NOT NULL
# );
#
# 4. src_property_management
#
# CREATE TABLE IF NOT EXISTS src_property_management (
#     observation_id VARCHAR PRIMARY KEY,
#     property_id VARCHAR NOT NULL,
#     company_id VARCHAR NOT NULL,
#     effective_at TIMESTAMPTZ NOT NULL
# );
#
# 5. src_incidents
#
# CREATE TABLE IF NOT EXISTS src_incidents (
#     incident_id VARCHAR PRIMARY KEY,
#     property_id VARCHAR NOT NULL,
#     reported_at TIMESTAMPTZ NOT NULL,
#     severity VARCHAR NOT NULL
# );
#
# 6. src_report_weeks
#
# CREATE TABLE IF NOT EXISTS src_report_weeks (
#     week_start DATE PRIMARY KEY
# );
#
# STAGE_2_REFERENCE_COUNTS
#   Returns one row per Stage 2 source table with its row count.
#
# PREVIEW_...
#   Returns the first 10 rows from that table so the reviewer can see the actual
#   values and fields stored in DuckDB.
# =============================================================================

STAGE_2_REFERENCE_COUNTS = """
SELECT 'src_companies' AS table_name, COUNT(*) AS row_count
FROM src_companies

UNION ALL

SELECT 'src_contractors', COUNT(*)
FROM src_contractors

UNION ALL

SELECT 'src_properties', COUNT(*)
FROM src_properties

UNION ALL

SELECT 'src_property_management', COUNT(*)
FROM src_property_management

UNION ALL

SELECT 'src_incidents', COUNT(*)
FROM src_incidents

UNION ALL

SELECT 'src_report_weeks', COUNT(*)
FROM src_report_weeks;
"""

PREVIEW_SRC_COMPANIES = """
SELECT *
FROM src_companies
LIMIT 10;
"""

PREVIEW_SRC_CONTRACTORS = """
SELECT *
FROM src_contractors
LIMIT 10;
"""

PREVIEW_SRC_PROPERTIES = """
SELECT *
FROM src_properties
LIMIT 10;
"""

PREVIEW_SRC_PROPERTY_MANAGEMENT = """
SELECT *
FROM src_property_management
LIMIT 10;
"""

PREVIEW_SRC_INCIDENTS = """
SELECT *
FROM src_incidents
LIMIT 10;
"""

PREVIEW_SRC_REPORT_WEEKS = """
SELECT *
FROM src_report_weeks
LIMIT 10;
"""


# =============================================================================
# STAGE 3 — RAW BATCH LANDING
# =============================================================================
#
# Aim:
#   Preserve every physical JSONL source line before parsing/cleaning it.
#
# Tables created:
#   1. ingestion_batches
#   2. raw_visit_lines

# Creation SQL (from sql/schema.sql)
#
# 1. ingestion_batches
#
# CREATE TABLE IF NOT EXISTS ingestion_batches (
#     batch_id VARCHAR PRIMARY KEY,
#     manifest_order INTEGER NOT NULL,
#     source_file VARCHAR NOT NULL,
#     snapshot_date DATE NOT NULL,
#     source_sha256 VARCHAR NOT NULL,
#     status VARCHAR NOT NULL
#         CHECK (status IN ('processing', 'landed', 'completed', 'failed')),
#     first_started_at TIMESTAMPTZ NOT NULL,
#     last_attempt_at TIMESTAMPTZ NOT NULL,
#     landed_at TIMESTAMPTZ,
#     validated_at TIMESTAMPTZ,
#     completed_at TIMESTAMPTZ,
#     attempt_count INTEGER NOT NULL DEFAULT 1,
#     input_lines INTEGER NOT NULL DEFAULT 0,
#     technically_valid_lines INTEGER NOT NULL DEFAULT 0,
#     technically_invalid_lines INTEGER NOT NULL DEFAULT 0,
#     unique_event_lines INTEGER NOT NULL DEFAULT 0,
#     duplicate_event_lines INTEGER NOT NULL DEFAULT 0,
#     applied_state_changes INTEGER NOT NULL DEFAULT 0,
#     stale_event_lines INTEGER NOT NULL DEFAULT 0,
#     business_quality_issues INTEGER NOT NULL DEFAULT 0,
#     error_message VARCHAR
# );
#
# 2. raw_visit_lines
#
# CREATE TABLE IF NOT EXISTS raw_visit_lines (
#     batch_id VARCHAR NOT NULL,
#     line_number INTEGER NOT NULL CHECK (line_number > 0),
#     raw_payload VARCHAR NOT NULL,
#     line_sha256 VARCHAR NOT NULL,
#     PRIMARY KEY (batch_id, line_number)
# );
#
# STAGE_3_RAW_LINE
#   Returns the exact stored raw payload and SHA256 for batch_01 line 1.
#
# STAGE_3_BATCH_COUNTS
#   Returns one row per batch showing manifest order, input line count, status
#   and processing-attempt count.
#
# PREVIEW_...
#   Returns the first 10 rows of each Stage 3 table.
# =============================================================================

STAGE_3_RAW_LINE = """
SELECT
    batch_id,
    line_number,
    raw_payload,
    line_sha256
FROM raw_visit_lines
WHERE batch_id = 'batch_01'
  AND line_number = 1;
"""

STAGE_3_BATCH_COUNTS = """
SELECT
    batch_id,
    manifest_order,
    input_lines,
    status,
    attempt_count
FROM ingestion_batches
ORDER BY manifest_order;
"""

PREVIEW_INGESTION_BATCHES = """
SELECT *
FROM ingestion_batches
LIMIT 10;
"""

PREVIEW_RAW_VISIT_LINES = """
SELECT *
FROM raw_visit_lines
LIMIT 10;
"""


# =============================================================================
# STAGE 4 — TECHNICAL VALIDATION
# =============================================================================
#
# Aim:
#   Separate technically valid event deliveries from malformed/incomplete lines.
#
# Tables created:
#   1. validated_visit_events
#   2. visit_event_rejects

# Creation SQL (from sql/schema.sql)
#
# 1. validated_visit_events
#
# CREATE TABLE IF NOT EXISTS validated_visit_events (
#     batch_id VARCHAR NOT NULL,
#     line_number INTEGER NOT NULL CHECK (line_number > 0),
#     event_id VARCHAR NOT NULL,
#     visit_id VARCHAR NOT NULL,
#     source_version BIGINT NOT NULL CHECK (source_version > 0),
#     operation VARCHAR NOT NULL CHECK (operation IN ('upsert', 'delete')),
#     updated_at TIMESTAMPTZ NOT NULL,
#     property_id VARCHAR,
#     contractor_id VARCHAR,
#     visitor_id VARCHAR,
#     started_at TIMESTAMPTZ,
#     ended_at TIMESTAMPTZ,
#     raw_payload VARCHAR NOT NULL,
#     PRIMARY KEY (batch_id, line_number)
# );
#
# 2. visit_event_rejects
#
# CREATE TABLE IF NOT EXISTS visit_event_rejects (
#     batch_id VARCHAR NOT NULL,
#     line_number INTEGER NOT NULL CHECK (line_number > 0),
#     raw_payload VARCHAR NOT NULL,
#     reason_codes VARCHAR NOT NULL,
#     rejection_reason VARCHAR NOT NULL,
#     rejected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
#     PRIMARY KEY (batch_id, line_number)
# );
#
# STAGE_4_VALID_EVENT
#   Returns the parsed/typed fields for event E00275 after technical validation.
#
# STAGE_4_REJECTS
#   Returns every technical reject with source batch/line, reason codes,
#   human-readable reason and raw payload.
#
# PREVIEW_...
#   Returns the first 10 rows from each Stage 4 table.
# =============================================================================

STAGE_4_VALID_EVENT = """
SELECT
    batch_id,
    line_number,
    event_id,
    visit_id,
    source_version,
    operation,
    property_id,
    contractor_id,
    visitor_id,
    started_at,
    ended_at
FROM validated_visit_events
WHERE event_id = 'E00275';
"""

STAGE_4_REJECTS = """
SELECT
    batch_id,
    line_number,
    reason_codes,
    rejection_reason,
    raw_payload
FROM visit_event_rejects
ORDER BY batch_id, line_number;
"""

PREVIEW_VALIDATED_VISIT_EVENTS = """
SELECT *
FROM validated_visit_events
LIMIT 10;
"""

PREVIEW_VISIT_EVENT_REJECTS = """
SELECT *
FROM visit_event_rejects
LIMIT 10;
"""


# =============================================================================
# STAGE 5 — DEDUPLICATION + VERSION PRECEDENCE + CURRENT STATE
# =============================================================================
#
# Aim:
#   Deduplicate repeated event deliveries, preserve unique event history and keep
#   one highest-version current state per visit.
#
# Tables created:
#   1. visit_events
#   2. visit_event_duplicates
#   3. visit_current_state

# Creation SQL (from sql/schema.sql)
#
# 1. visit_events
#
# CREATE TABLE IF NOT EXISTS visit_events (
#     event_id VARCHAR PRIMARY KEY,
#     visit_id VARCHAR NOT NULL,
#     source_version BIGINT NOT NULL CHECK (source_version > 0),
#     operation VARCHAR NOT NULL CHECK (operation IN ('upsert', 'delete')),
#     updated_at TIMESTAMPTZ NOT NULL,
#     property_id VARCHAR,
#     contractor_id VARCHAR,
#     visitor_id VARCHAR,
#     started_at TIMESTAMPTZ,
#     ended_at TIMESTAMPTZ,
#     first_batch_id VARCHAR NOT NULL,
#     first_line_number INTEGER NOT NULL CHECK (first_line_number > 0),
#     state_action VARCHAR NOT NULL CHECK (state_action IN ('applied', 'stale')),
#     prior_source_version BIGINT,
#     raw_payload VARCHAR NOT NULL
# );
#
# 2. visit_event_duplicates
#
# CREATE TABLE IF NOT EXISTS visit_event_duplicates (
#     batch_id VARCHAR NOT NULL,
#     line_number INTEGER NOT NULL CHECK (line_number > 0),
#     event_id VARCHAR NOT NULL,
#     canonical_batch_id VARCHAR NOT NULL,
#     canonical_line_number INTEGER NOT NULL CHECK (canonical_line_number > 0),
#     PRIMARY KEY (batch_id, line_number)
# );
#
# 3. visit_current_state
#
# CREATE TABLE IF NOT EXISTS visit_current_state (
#     visit_id VARCHAR PRIMARY KEY,
#     source_version BIGINT NOT NULL CHECK (source_version > 0),
#     operation VARCHAR NOT NULL CHECK (operation IN ('upsert', 'delete')),
#     is_deleted BOOLEAN NOT NULL,
#     winning_event_id VARCHAR NOT NULL,
#     updated_at TIMESTAMPTZ NOT NULL,
#     property_id VARCHAR,
#     contractor_id VARCHAR,
#     visitor_id VARCHAR,
#     started_at TIMESTAMPTZ,
#     ended_at TIMESTAMPTZ,
#     source_batch_id VARCHAR NOT NULL,
#     source_line_number INTEGER NOT NULL CHECK (source_line_number > 0)
# );
#
# Important example:
#   V02
#     E00002 v1 upsert -> applied
#     E00683 v2 delete -> applied
#     E01943 v1 upsert -> stale
#
# STAGE_5_V02_EVENT_HISTORY
#   Returns the unique V02 event history and each event's state_action.
#
# STAGE_5_V02_DUPLICATE
#   Returns the repeated physical delivery of E00002 and points to its canonical
#   first occurrence.
#
# STAGE_5_V02_CURRENT_STATE
#   Returns the winning V02 current state: version 2 delete tombstone.
#
# STAGE_5_CORRECTION_HISTORY
#   Returns the v1 and v2 correction history for SYN_V00256.
#
# STAGE_5_CORRECTION_WINNER
#   Returns only the current winning state for SYN_V00256.
#
# PREVIEW_...
#   Returns the first 10 rows from each Stage 5 table.
# =============================================================================

STAGE_5_V02_EVENT_HISTORY = """
SELECT
    event_id,
    visit_id,
    source_version,
    operation,
    state_action,
    prior_source_version,
    first_batch_id,
    first_line_number
FROM visit_events
WHERE visit_id = 'V02'
ORDER BY source_version, event_id;
"""

STAGE_5_V02_DUPLICATE = """
SELECT
    batch_id,
    line_number,
    event_id,
    canonical_batch_id,
    canonical_line_number
FROM visit_event_duplicates
WHERE event_id = 'E00002';
"""

STAGE_5_V02_CURRENT_STATE = """
SELECT
    visit_id,
    source_version,
    operation,
    is_deleted,
    winning_event_id,
    updated_at
FROM visit_current_state
WHERE visit_id = 'V02';
"""

STAGE_5_CORRECTION_HISTORY = """
SELECT
    event_id,
    visit_id,
    source_version,
    started_at,
    ended_at,
    state_action,
    prior_source_version,
    first_batch_id,
    first_line_number
FROM visit_events
WHERE visit_id = 'SYN_V00256'
ORDER BY source_version;
"""

STAGE_5_CORRECTION_WINNER = """
SELECT
    visit_id,
    source_version,
    started_at,
    ended_at,
    winning_event_id
FROM visit_current_state
WHERE visit_id = 'SYN_V00256';
"""

PREVIEW_VISIT_EVENTS = """
SELECT *
FROM visit_events
LIMIT 10;
"""

PREVIEW_VISIT_EVENT_DUPLICATES = """
SELECT *
FROM visit_event_duplicates
LIMIT 10;
"""

PREVIEW_VISIT_CURRENT_STATE_STAGE5 = """
SELECT *
FROM visit_current_state
LIMIT 10;
"""


# =============================================================================
# STAGE 6 — BUSINESS QUALITY
# =============================================================================
#
# Aim:
#   Identify technically valid current visits that should not be used for
#   business reporting.
#
# Objects created:
#   1. visit_quality_issues          (table)
#   2. reportable_current_visits    (view)

# Creation SQL (from sql/schema.sql)
#
# 1. visit_quality_issues  [TABLE]
#
# CREATE TABLE IF NOT EXISTS visit_quality_issues (
#     visit_id VARCHAR NOT NULL,
#     issue_code VARCHAR NOT NULL
#         CHECK (
#             issue_code IN (
#                 'unknown_property',
#                 'unknown_contractor',
#                 'end_before_start'
#             )
#         ),
#     winning_event_id VARCHAR NOT NULL,
#     source_batch_id VARCHAR NOT NULL,
#     source_line_number INTEGER NOT NULL CHECK (source_line_number > 0),
#     property_id VARCHAR,
#     contractor_id VARCHAR,
#     started_at TIMESTAMPTZ,
#     ended_at TIMESTAMPTZ,
#     issue_detail VARCHAR NOT NULL,
#     PRIMARY KEY (visit_id, issue_code)
# );
#
# 2. reportable_current_visits  [VIEW]
#
# CREATE OR REPLACE VIEW reportable_current_visits AS
# SELECT s.*
# FROM visit_current_state AS s
# WHERE s.is_deleted = FALSE
#   AND NOT EXISTS (
#       SELECT 1
#       FROM visit_quality_issues AS q
#       WHERE q.visit_id = s.visit_id
#   );
#
# STAGE_6_QUALITY_ISSUES
#   Returns V11/V12/V13 with issue code, source business fields and explanation.
#
# STAGE_6_COUNTS
#   Returns:
#     - active current visits;
#     - visits affected by business-quality issues;
#     - final reportable active visits.
#
# PREVIEW_...
#   Returns the first 10 rows from the issue table and reportable view.
# =============================================================================

STAGE_6_QUALITY_ISSUES = """
SELECT
    visit_id,
    issue_code,
    winning_event_id,
    property_id,
    contractor_id,
    started_at,
    ended_at,
    issue_detail
FROM visit_quality_issues
ORDER BY visit_id, issue_code;
"""

STAGE_6_COUNTS = """
SELECT
    (SELECT COUNT(*)
     FROM visit_current_state
     WHERE is_deleted = FALSE) AS active_current_visits,

    (SELECT COUNT(DISTINCT visit_id)
     FROM visit_quality_issues) AS visits_with_quality_issues,

    (SELECT COUNT(*)
     FROM reportable_current_visits) AS reportable_current_visits;
"""

PREVIEW_VISIT_QUALITY_ISSUES = """
SELECT *
FROM visit_quality_issues
LIMIT 10;
"""

PREVIEW_REPORTABLE_CURRENT_VISITS = """
SELECT *
FROM reportable_current_visits
LIMIT 10;
"""


# =============================================================================
# STAGE 7 — PROPERTY-MANAGEMENT HISTORY
# =============================================================================
#
# Aim:
#   Convert management observations into effective-dated historical intervals
#   and expose one current manager per property.
#
# Objects created:
#   1. property_management_intervals
#   2. current_property_management

# Creation SQL (from sql/schema.sql)
#
# 1. property_management_intervals  [TABLE]
#
# CREATE TABLE IF NOT EXISTS property_management_intervals (
#     property_id VARCHAR NOT NULL,
#     company_id VARCHAR NOT NULL,
#     valid_from TIMESTAMPTZ NOT NULL,
#     valid_to TIMESTAMPTZ,
#     start_observation_id VARCHAR NOT NULL,
#     source_observation_count INTEGER NOT NULL CHECK (source_observation_count > 0),
#     PRIMARY KEY (property_id, valid_from)
# );
#
# 2. current_property_management  [VIEW]
#
# CREATE OR REPLACE VIEW current_property_management AS
# SELECT
#     i.property_id,
#     p.property_name,
#     i.company_id,
#     c.company_name,
#     i.valid_from AS managed_from,
#     i.start_observation_id,
#     i.source_observation_count
# FROM property_management_intervals AS i
# JOIN src_properties AS p
#     ON p.property_id = i.property_id
# JOIN src_companies AS c
#     ON c.company_id = i.company_id
# WHERE i.valid_to IS NULL;
#
# STAGE_7_P03_INTERVALS
#   Returns P03's historical management intervals.
#
# STAGE_7_P03_BOUNDARY
#   Returns four boundary records proving that 12:59 belongs to C02 and exactly
#   13:00 belongs to C03.
#
# PREVIEW_...
#   Returns the first 10 rows from each Stage 7 object.
# =============================================================================

STAGE_7_P03_INTERVALS = """
SELECT
    property_id,
    company_id,
    valid_from,
    valid_to,
    start_observation_id,
    source_observation_count
FROM property_management_intervals
WHERE property_id = 'P03'
ORDER BY valid_from;
"""

STAGE_7_P03_BOUNDARY = """
SELECT
    'visit' AS record_type,
    visit_id AS record_id,
    property_id,
    started_at AS business_time,
    company_id
FROM fact_visits
WHERE visit_id IN ('V09', 'V10')

UNION ALL

SELECT
    'incident' AS record_type,
    incident_id AS record_id,
    property_id,
    reported_at AS business_time,
    company_id
FROM fact_incidents
WHERE incident_id IN ('I06', 'I07')

ORDER BY business_time, record_type, record_id;
"""

PREVIEW_PROPERTY_MANAGEMENT_INTERVALS = """
SELECT *
FROM property_management_intervals
LIMIT 10;
"""

PREVIEW_CURRENT_PROPERTY_MANAGEMENT = """
SELECT *
FROM current_property_management
LIMIT 10;
"""


# =============================================================================
# STAGE 8 — ANALYTICAL FACTS
# =============================================================================
#
# Aim:
#   Build analytics-ready visit and incident facts with historical company,
#   property-local reporting week and DST-safe completed duration.
#
# Tables created:
#   1. fact_visits
#   2. fact_incidents

# Creation SQL (from sql/schema.sql)
#
# 1. fact_visits
#
# CREATE TABLE IF NOT EXISTS fact_visits (
#     visit_id VARCHAR PRIMARY KEY,
#     property_id VARCHAR NOT NULL,
#     property_name VARCHAR NOT NULL,
#     property_timezone VARCHAR NOT NULL,
#     contractor_id VARCHAR NOT NULL,
#     contractor_name VARCHAR NOT NULL,
#     visitor_id VARCHAR NOT NULL,
#     company_id VARCHAR NOT NULL,
#     company_name VARCHAR NOT NULL,
#     started_at TIMESTAMPTZ NOT NULL,
#     ended_at TIMESTAMPTZ,
#     local_started_at TIMESTAMP NOT NULL,
#     local_ended_at TIMESTAMP,
#     week_start_local DATE NOT NULL,
#     is_requested_week BOOLEAN NOT NULL,
#     is_unfinished BOOLEAN NOT NULL,
#     completed_hours DOUBLE,
#     source_version BIGINT NOT NULL,
#     winning_event_id VARCHAR NOT NULL,
#     CHECK (completed_hours IS NULL OR completed_hours >= 0)
# );
#
# 2. fact_incidents
#
# CREATE TABLE IF NOT EXISTS fact_incidents (
#     incident_id VARCHAR PRIMARY KEY,
#     property_id VARCHAR NOT NULL,
#     property_name VARCHAR NOT NULL,
#     property_timezone VARCHAR NOT NULL,
#     company_id VARCHAR NOT NULL,
#     company_name VARCHAR NOT NULL,
#     reported_at TIMESTAMPTZ NOT NULL,
#     local_reported_at TIMESTAMP NOT NULL,
#     week_start_local DATE NOT NULL,
#     is_requested_week BOOLEAN NOT NULL,
#     severity VARCHAR NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
#     is_high BOOLEAN NOT NULL
# );
#
# STAGE_8_LOCAL_WEEK_BOUNDARY
#   Returns V14/V01 to prove one UTC minute crosses a property-local Monday
#   reporting-week boundary.
#
# STAGE_8_DST_DURATION
#   Returns V07/V16 to prove local wall-clock display can differ from actual
#   elapsed duration across daylight-saving change.
#
# STAGE_8_REPORT_KPIS
#   Returns the six headline metrics used by the HTML report:
#     visits, distinct visitors, completed hours, unfinished visits,
#     incidents, high-severity incidents.
#
# PREVIEW_...
#   Returns the first 10 rows from each Stage 8 fact table.
# =============================================================================

STAGE_8_LOCAL_WEEK_BOUNDARY = """
SELECT
    visit_id,
    property_id,
    property_timezone,
    started_at,
    local_started_at,
    week_start_local
FROM fact_visits
WHERE visit_id IN ('V14', 'V01')
ORDER BY started_at;
"""

STAGE_8_DST_DURATION = """
SELECT
    visit_id,
    property_timezone,
    started_at,
    ended_at,
    local_started_at,
    local_ended_at,
    completed_hours
FROM fact_visits
WHERE visit_id IN ('V07', 'V16')
ORDER BY visit_id;
"""

STAGE_8_REPORT_KPIS = """
SELECT
    (SELECT COUNT(*)
     FROM fact_visits
     WHERE is_requested_week = TRUE) AS visits,

    (SELECT COUNT(DISTINCT visitor_id)
     FROM fact_visits
     WHERE is_requested_week = TRUE) AS distinct_visitors,

    (SELECT ROUND(SUM(COALESCE(completed_hours, 0)), 2)
     FROM fact_visits
     WHERE is_requested_week = TRUE) AS completed_hours,

    (SELECT SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
     FROM fact_visits
     WHERE is_requested_week = TRUE) AS unfinished_visits,

    (SELECT COUNT(*)
     FROM fact_incidents
     WHERE is_requested_week = TRUE) AS incidents,

    (SELECT SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
     FROM fact_incidents
     WHERE is_requested_week = TRUE) AS high_severity_incidents;
"""

PREVIEW_FACT_VISITS_STAGE8 = """
SELECT *
FROM fact_visits
LIMIT 10;
"""

PREVIEW_FACT_INCIDENTS = """
SELECT *
FROM fact_incidents
LIMIT 10;
"""


# =============================================================================
# STAGE 9 — REPORTING MARTS
# =============================================================================
#
# Aim:
#   Aggregate facts into stable report grains used directly by the final report.
#
# Tables created:
#   1. rpt_company_week
#   2. rpt_company_period
#   3. rpt_property_week
#   4. rpt_contractor

# Creation SQL (from sql/schema.sql)
#
# 1. rpt_company_week
#
# CREATE TABLE IF NOT EXISTS rpt_company_week (
#     company_id VARCHAR NOT NULL,
#     company_name VARCHAR NOT NULL,
#     week_start DATE NOT NULL,
#     visit_count BIGINT NOT NULL,
#     distinct_visitors BIGINT NOT NULL,
#     completed_visit_hours DOUBLE NOT NULL,
#     unfinished_visits BIGINT NOT NULL,
#     incident_count BIGINT NOT NULL,
#     high_severity_incidents BIGINT NOT NULL,
#     PRIMARY KEY (company_id, week_start)
# );
#
# 2. rpt_company_period
#
# CREATE TABLE IF NOT EXISTS rpt_company_period (
#     company_id VARCHAR PRIMARY KEY,
#     company_name VARCHAR NOT NULL,
#     visit_count BIGINT NOT NULL,
#     distinct_visitors BIGINT NOT NULL,
#     completed_visit_hours DOUBLE NOT NULL,
#     unfinished_visits BIGINT NOT NULL,
#     incident_count BIGINT NOT NULL,
#     high_severity_incidents BIGINT NOT NULL
# );
#
# 3. rpt_property_week
#
# CREATE TABLE IF NOT EXISTS rpt_property_week (
#     property_id VARCHAR NOT NULL,
#     property_name VARCHAR NOT NULL,
#     week_start DATE NOT NULL,
#     visit_count BIGINT NOT NULL,
#     distinct_visitors BIGINT NOT NULL,
#     completed_visit_hours DOUBLE NOT NULL,
#     unfinished_visits BIGINT NOT NULL,
#     incident_count BIGINT NOT NULL,
#     high_severity_incidents BIGINT NOT NULL,
#     has_activity BOOLEAN NOT NULL,
#     PRIMARY KEY (property_id, week_start)
# );
#
# 4. rpt_contractor
#
# CREATE TABLE IF NOT EXISTS rpt_contractor (
#     contractor_id VARCHAR PRIMARY KEY,
#     contractor_name VARCHAR NOT NULL,
#     visit_count BIGINT NOT NULL,
#     distinct_visitors BIGINT NOT NULL,
#     property_count BIGINT NOT NULL,
#     completed_visit_hours DOUBLE NOT NULL,
#     unfinished_visits BIGINT NOT NULL
# );
#
# STAGE_9_COMPANY_PERIOD
#   Returns one row per company for the full requested reporting period.
#
# STAGE_9_ZERO_ACTIVITY
#   Returns all property/week rows with no visit or incident activity.
#
# STAGE_9_DISTINCT_NON_ADDITIVE
#   Returns the true overall distinct visitor count and the larger sum of
#   company-level distinct counts, proving distinct counts are non-additive.
#
# STAGE_9_CONTRACTORS
#   Returns the contractor-period mart ordered by visit count.
#
# PREVIEW_...
#   Returns the first 10 rows from each Stage 9 mart table.
# =============================================================================

STAGE_9_COMPANY_PERIOD = """
SELECT
    company_id,
    company_name,
    visit_count,
    distinct_visitors,
    completed_visit_hours,
    unfinished_visits,
    incident_count,
    high_severity_incidents
FROM rpt_company_period
ORDER BY company_id;
"""

STAGE_9_ZERO_ACTIVITY = """
SELECT
    property_id,
    property_name,
    week_start,
    visit_count,
    incident_count
FROM rpt_property_week
WHERE has_activity = FALSE
ORDER BY property_id, week_start;
"""

STAGE_9_DISTINCT_NON_ADDITIVE = """
SELECT
    (SELECT COUNT(DISTINCT visitor_id)
     FROM fact_visits
     WHERE is_requested_week = TRUE) AS overall_distinct_visitors,

    (SELECT SUM(distinct_visitors)
     FROM rpt_company_period) AS sum_of_company_distinct_visitors;
"""

STAGE_9_CONTRACTORS = """
SELECT
    contractor_id,
    contractor_name,
    visit_count,
    distinct_visitors,
    property_count,
    completed_visit_hours,
    unfinished_visits
FROM rpt_contractor
ORDER BY visit_count DESC, contractor_id;
"""

PREVIEW_RPT_COMPANY_WEEK = """
SELECT *
FROM rpt_company_week
LIMIT 10;
"""

PREVIEW_RPT_COMPANY_PERIOD = """
SELECT *
FROM rpt_company_period
LIMIT 10;
"""

PREVIEW_RPT_PROPERTY_WEEK = """
SELECT *
FROM rpt_property_week
LIMIT 10;
"""

PREVIEW_RPT_CONTRACTOR = """
SELECT *
FROM rpt_contractor
LIMIT 10;
"""


# =============================================================================
# STAGE 10 — REPORT BUILD
# =============================================================================
#
# Aim:
#   Record metadata for the generated HTML report and CSV extracts.
#
# Table created:
#   1. report_builds

# Creation SQL (from sql/schema.sql)
#
# 1. report_builds
#
# CREATE TABLE IF NOT EXISTS report_builds (
#     report_name VARCHAR PRIMARY KEY,
#     generated_at TIMESTAMPTZ NOT NULL,
#     report_path VARCHAR NOT NULL,
#     extract_directory VARCHAR NOT NULL,
#     latest_batch_id VARCHAR NOT NULL,
#     first_report_week DATE NOT NULL,
#     last_report_week DATE NOT NULL
# );
#
# STAGE_10_REPORT_BUILD
#   Returns report name, generated timestamp, report/extract paths, latest batch
#   and requested reporting-period boundaries.
#
# PREVIEW_REPORT_BUILDS
#   Returns up to the first 10 report-build metadata rows.
# =============================================================================

STAGE_10_REPORT_BUILD = """
SELECT
    report_name,
    generated_at,
    report_path,
    extract_directory,
    latest_batch_id,
    first_report_week,
    last_report_week
FROM report_builds;
"""

PREVIEW_REPORT_BUILDS = """
SELECT *
FROM report_builds
LIMIT 10;
"""


# =============================================================================
# QUERY RESULT EXPLANATIONS
# =============================================================================
#
# The title used in RUN.append(...) maps to a short explanation printed before
# the SQL. This makes the terminal output easier to explain during a review.
# =============================================================================

QUERY_RETURNS: dict[str, str] = {
    "Show all DuckDB tables":
        "Returns the names of all current DuckDB tables/views in the completed model.",
    "Describe visit_current_state":
        "Returns visit_current_state column names, types, nullability and key metadata.",
    "Describe fact_visits":
        "Returns fact_visits column names, types, nullability and key metadata.",
    "Preview visit_current_state":
        "Returns the first 10 rows from the current visit-state table.",
    "Preview fact_visits":
        "Returns the first 10 rows from the analytical visit fact table.",

    "Stage 2 - reference table counts":
        "Returns the row count for each of the six typed CSV reference/source tables.",
    "Stage 2 - preview src_companies":
        "Returns the first 10 company reference rows.",
    "Stage 2 - preview src_contractors":
        "Returns the first 10 contractor reference rows.",
    "Stage 2 - preview src_properties":
        "Returns the first 10 property reference rows, including property timezone.",
    "Stage 2 - preview src_property_management":
        "Returns the first 10 raw property-management observations.",
    "Stage 2 - preview src_incidents":
        "Returns the first 10 incident source rows.",
    "Stage 2 - preview src_report_weeks":
        "Returns the requested reporting-week rows.",

    "Stage 3 - source line batch_01:1":
        "Returns the exact preserved raw JSONL line and its SHA256 fingerprint.",
    "Stage 3 - ingestion batches":
        "Returns the six ingestion batches with order, source-line count, status and attempts.",
    "Stage 3 - preview ingestion_batches":
        "Returns the first 10 rows from batch-control metadata.",
    "Stage 3 - preview raw_visit_lines":
        "Returns the first 10 physically preserved source lines.",

    "Stage 4 - E00275 technically valid":
        "Returns the parsed and typed Stage 4 representation of source event E00275.",
    "Stage 4 - technical rejects":
        "Returns every technically invalid source line with reason and raw evidence.",
    "Stage 4 - preview validated_visit_events":
        "Returns the first 10 technically valid event-delivery rows.",
    "Stage 4 - preview visit_event_rejects":
        "Returns the first 10 technical reject rows.",

    "Stage 5 - V02 event history":
        "Returns V02's unique event history, versions, operations and applied/stale decisions.",
    "Stage 5 - E00002 duplicate delivery":
        "Returns the repeated E00002 delivery and its canonical first occurrence.",
    "Stage 5 - V02 current tombstone":
        "Returns the winning V02 current state, which is the version-2 delete tombstone.",
    "Stage 5 - correction history SYN_V00256":
        "Returns both versions of SYN_V00256 and shows the correction history.",
    "Stage 5 - correction winner SYN_V00256":
        "Returns only the winning/current version of SYN_V00256.",
    "Stage 5 - preview visit_events":
        "Returns the first 10 globally unique event-history rows.",
    "Stage 5 - preview visit_event_duplicates":
        "Returns the first 10 duplicate-delivery audit rows.",
    "Stage 5 - preview visit_current_state":
        "Returns the first 10 highest-version visit states.",

    "Stage 6 - V11/V12/V13 quality issues":
        "Returns the three business-quality failures and why they are excluded from reporting.",
    "Stage 6 - active/quality/reportable counts":
        "Returns active-current count, affected-quality count and reportable-current count.",
    "Stage 6 - preview visit_quality_issues":
        "Returns the first 10 business-quality issue rows.",
    "Stage 6 - preview reportable_current_visits":
        "Returns the first 10 clean active visits used by analytics.",

    "Stage 7 - P03 management intervals":
        "Returns P03's effective-dated historical company-management intervals.",
    "Stage 7 - P03 12:59/13:00 boundary":
        "Returns visit/incident records immediately before and at the P03 transfer boundary.",
    "Stage 7 - preview property_management_intervals":
        "Returns the first 10 reconstructed historical management intervals.",
    "Stage 7 - preview current_property_management":
        "Returns the first 10 current property-to-company management rows.",

    "Stage 8 - local week boundary":
        "Returns V14/V01 UTC time, property-local time and derived local Monday week.",
    "Stage 8 - DST elapsed duration":
        "Returns V07/V16 UTC/local times and DST-safe elapsed completed hours.",
    "Stage 8 - final KPI facts":
        "Returns the six requested-period headline metrics later displayed in the HTML report.",
    "Stage 8 - preview fact_visits":
        "Returns the first 10 reportable visit fact rows.",
    "Stage 8 - preview fact_incidents":
        "Returns the first 10 incident fact rows.",

    "Stage 9 - company period mart":
        "Returns the company requested-period summary used by the final report.",
    "Stage 9 - zero-activity property weeks":
        "Returns property/week combinations where both visits and incidents are zero.",
    "Stage 9 - distinct visitor non-additivity":
        "Returns true overall distinct visitors versus the sum of company distinct counts.",
    "Stage 9 - contractor mart":
        "Returns contractor requested-period metrics ordered by visit count.",
    "Stage 9 - preview rpt_company_week":
        "Returns the first 10 company-by-week mart rows.",
    "Stage 9 - preview rpt_company_period":
        "Returns the three company-period mart rows.",
    "Stage 9 - preview rpt_property_week":
        "Returns the first 10 property-by-week mart rows.",
    "Stage 9 - preview rpt_contractor":
        "Returns the first 10 contractor mart rows.",

    "Stage 10 - report build metadata":
        "Returns metadata describing the generated operations report build.",
    "Stage 10 - preview report_builds":
        "Returns up to 10 report-build metadata rows.",
}


# =============================================================================
# RUN SECTION
# =============================================================================
#
# Uncomment ONE OR MORE lines below.
# Only remove the leading "# ".
#
# Example:
#
#     RUN.append(("Stage 3 - source line batch_01:1", STAGE_3_RAW_LINE))
#
# Then run:
#
#     python -m src.query_examples
#
# =============================================================================

RUN: list[tuple[str, str]] = []

# --- General/schema -----------------------------------------------------------
# RUN.append(("Show all DuckDB tables", SHOW_ALL_TABLES))
# RUN.append(("Describe visit_current_state", DESCRIBE_VISIT_CURRENT_STATE))
# RUN.append(("Describe fact_visits", DESCRIBE_FACT_VISITS))
# RUN.append(("Preview visit_current_state", PREVIEW_VISIT_CURRENT_STATE))
# RUN.append(("Preview fact_visits", PREVIEW_FACT_VISITS))

# --- Stage 2: 6 typed reference/source tables --------------------------------
# RUN.append(("Stage 2 - reference table counts", STAGE_2_REFERENCE_COUNTS))
# RUN.append(("Stage 2 - preview src_companies", PREVIEW_SRC_COMPANIES))
# RUN.append(("Stage 2 - preview src_contractors", PREVIEW_SRC_CONTRACTORS))
# RUN.append(("Stage 2 - preview src_properties", PREVIEW_SRC_PROPERTIES))
# RUN.append(("Stage 2 - preview src_property_management", PREVIEW_SRC_PROPERTY_MANAGEMENT))
# RUN.append(("Stage 2 - preview src_incidents", PREVIEW_SRC_INCIDENTS))
# RUN.append(("Stage 2 - preview src_report_weeks", PREVIEW_SRC_REPORT_WEEKS))

# --- Stage 3: ingestion_batches + raw_visit_lines -----------------------------
# RUN.append(("Stage 3 - source line batch_01:1", STAGE_3_RAW_LINE))
# RUN.append(("Stage 3 - ingestion batches", STAGE_3_BATCH_COUNTS))
# RUN.append(("Stage 3 - preview ingestion_batches", PREVIEW_INGESTION_BATCHES))
# RUN.append(("Stage 3 - preview raw_visit_lines", PREVIEW_RAW_VISIT_LINES))

# --- Stage 4: validated_visit_events + visit_event_rejects --------------------
# RUN.append(("Stage 4 - E00275 technically valid", STAGE_4_VALID_EVENT))
# RUN.append(("Stage 4 - technical rejects", STAGE_4_REJECTS))
# RUN.append(("Stage 4 - preview validated_visit_events", PREVIEW_VALIDATED_VISIT_EVENTS))
# RUN.append(("Stage 4 - preview visit_event_rejects", PREVIEW_VISIT_EVENT_REJECTS))

# --- Stage 5: visit_events + duplicates + current state -----------------------
# RUN.append(("Stage 5 - V02 event history", STAGE_5_V02_EVENT_HISTORY))
# RUN.append(("Stage 5 - E00002 duplicate delivery", STAGE_5_V02_DUPLICATE))
# RUN.append(("Stage 5 - V02 current tombstone", STAGE_5_V02_CURRENT_STATE))
# RUN.append(("Stage 5 - correction history SYN_V00256", STAGE_5_CORRECTION_HISTORY))
# RUN.append(("Stage 5 - correction winner SYN_V00256", STAGE_5_CORRECTION_WINNER))
# RUN.append(("Stage 5 - preview visit_events", PREVIEW_VISIT_EVENTS))
# RUN.append(("Stage 5 - preview visit_event_duplicates", PREVIEW_VISIT_EVENT_DUPLICATES))
# RUN.append(("Stage 5 - preview visit_current_state", PREVIEW_VISIT_CURRENT_STATE_STAGE5))

# --- Stage 6: quality issues + reportable-current view ------------------------
# RUN.append(("Stage 6 - V11/V12/V13 quality issues", STAGE_6_QUALITY_ISSUES))
# RUN.append(("Stage 6 - active/quality/reportable counts", STAGE_6_COUNTS))
# RUN.append(("Stage 6 - preview visit_quality_issues", PREVIEW_VISIT_QUALITY_ISSUES))
# RUN.append(("Stage 6 - preview reportable_current_visits", PREVIEW_REPORTABLE_CURRENT_VISITS))

# --- Stage 7: historical + current property management ------------------------
# RUN.append(("Stage 7 - P03 management intervals", STAGE_7_P03_INTERVALS))
# RUN.append(("Stage 7 - P03 12:59/13:00 boundary", STAGE_7_P03_BOUNDARY))
# RUN.append(("Stage 7 - preview property_management_intervals", PREVIEW_PROPERTY_MANAGEMENT_INTERVALS))
# RUN.append(("Stage 7 - preview current_property_management", PREVIEW_CURRENT_PROPERTY_MANAGEMENT))

# --- Stage 8: visit + incident analytical facts -------------------------------
# RUN.append(("Stage 8 - local week boundary", STAGE_8_LOCAL_WEEK_BOUNDARY))
# RUN.append(("Stage 8 - DST elapsed duration", STAGE_8_DST_DURATION))
# RUN.append(("Stage 8 - final KPI facts", STAGE_8_REPORT_KPIS))
# RUN.append(("Stage 8 - preview fact_visits", PREVIEW_FACT_VISITS_STAGE8))
# RUN.append(("Stage 8 - preview fact_incidents", PREVIEW_FACT_INCIDENTS))

# --- Stage 9: company/property/contractor reporting marts ---------------------
# RUN.append(("Stage 9 - company period mart", STAGE_9_COMPANY_PERIOD))
# RUN.append(("Stage 9 - zero-activity property weeks", STAGE_9_ZERO_ACTIVITY))
# RUN.append(("Stage 9 - distinct visitor non-additivity", STAGE_9_DISTINCT_NON_ADDITIVE))
# RUN.append(("Stage 9 - contractor mart", STAGE_9_CONTRACTORS))
# RUN.append(("Stage 9 - preview rpt_company_week", PREVIEW_RPT_COMPANY_WEEK))
# RUN.append(("Stage 9 - preview rpt_company_period", PREVIEW_RPT_COMPANY_PERIOD))
# RUN.append(("Stage 9 - preview rpt_property_week", PREVIEW_RPT_PROPERTY_WEEK))
# RUN.append(("Stage 9 - preview rpt_contractor", PREVIEW_RPT_CONTRACTOR))

# --- Stage 10: report build metadata ------------------------------------------
# RUN.append(("Stage 10 - report build metadata", STAGE_10_REPORT_BUILD))
# RUN.append(("Stage 10 - preview report_builds", PREVIEW_REPORT_BUILDS))


def _print_rows(
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> None:
    if not rows:
        print("(no rows)")
        return

    rendered = [
        ["" if value is None else str(value) for value in row]
        for row in rows
    ]

    widths = [
        max(
            len(str(columns[index])),
            max(len(row[index]) for row in rendered),
        )
        for index in range(len(columns))
    ]

    # Prevent one large field such as raw JSON from making the terminal absurdly
    # wide. The field is truncated only for display, not in the database.
    widths = [min(width, 80) for width in widths]

    def render_row(values: Sequence[object]) -> str:
        cells = []

        for index, value in enumerate(values):
            text = "" if value is None else str(value)

            if len(text) > widths[index]:
                text = text[: max(0, widths[index] - 3)] + "..."

            cells.append(text.ljust(widths[index]))

        return " | ".join(cells)

    separator = "-+-".join("-" * width for width in widths)

    print(render_row(columns))
    print(separator)

    for row in rows:
        print(render_row(row))


def _run_query(connection, *, title: str, sql: str) -> None:
    print()
    print("=" * 118)
    print(title)
    print("=" * 118)

    explanation = QUERY_RETURNS.get(
        title,
        "Returns the result set produced by this read-only DuckDB query.",
    )

    print("Returns:")
    print(f"  {explanation}")
    print()
    print("SQL:")
    print(sql.strip())
    print("-" * 118)

    cursor = connection.execute(sql)

    if cursor.description is None:
        print("Query completed.")
        return

    columns = [description[0] for description in cursor.description]
    rows = cursor.fetchall()

    _print_rows(columns, rows)
    print(f"\nRows returned: {len(rows)}")


def main() -> None:
    if not RUN:
        print("=" * 96)
        print("DUCKDB QUERY EXAMPLES")
        print("=" * 96)
        print(
            "No query is selected.\n\n"
            "Open src/query_examples.py and uncomment one or more RUN.append(...) "
            "lines near the bottom of the file.\n\n"
            "Examples:\n"
            "    # Show the database objects\n"
            "    RUN.append((\"Show all DuckDB tables\", SHOW_ALL_TABLES))\n\n"
            "    # Show actual rows from Stage 5 current state\n"
            "    RUN.append((\"Stage 5 - preview visit_current_state\", "
            "PREVIEW_VISIT_CURRENT_STATE_STAGE5))\n\n"
            "Then run:\n"
            "    python -m src.query_examples"
        )
        return

    connection = connect_database(read_only=True)

    try:
        for title, sql in RUN:
            _run_query(
                connection,
                title=title,
                sql=sql,
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
