# Production-scale design

## Purpose

The submitted implementation is intentionally local and reproducible: Python,
DuckDB, SQL, pytest and a self-contained HTML report.

This document explains how the same business rules could evolve for a production
environment with millions of records, higher ingestion frequency, multiple
producers and stronger operational requirements.

This is a **proposed reference architecture** for the assignment solution. It is
not a claim about Onside's current production architecture.

---

## 1. Scaling to millions of records

Millions of rows can be large enough that I would stop treating the pipeline as a
single local-file workflow, but it is not automatically a reason to introduce a
large streaming platform.

The main production questions are not simply "can the database store seven
million rows?" They are:

- how frequently does new data arrive?
- how many producers write it?
- how quickly must reports update?
- how often are old visits corrected or deleted?
- do we need replay/backfill?
- what happens when one batch or event fails?
- what security controls apply to the data?
- how do we observe freshness, failures and cost?

For datasets with millions of rows, a cloud object store plus a warehouse such
as Snowflake can comfortably support the data volume. I would choose batch or
micro-batch ingestion unless the business truly requires second-level streaming.

---

## 2. Proposed production architecture

```text
Operational applications / APIs / exports
                  |
                  v
        Ingestion endpoint or file drop
                  |
                  v
        Amazon S3 immutable raw zone
        source/date/batch partitioning
                  |
                  v
         Event notification / routing
          SQS (+ EventBridge if needed)
                  |
                  v
         Lightweight processing layer
                Lambda
       validation / metadata / routing
                  |
                  v
       Snowflake RAW / landing tables
        Snowpipe or scheduled COPY
                  |
                  v
      SQL / dbt transformation layers
        STAGING -> INTERMEDIATE -> MART
                  |
                  v
       BI / dashboards / operations API
```

### Why this shape?

**S3** becomes the immutable source-of-truth landing zone. Source files/events are
kept before transformation so failed loads can be replayed and historical source
evidence remains available.

**SQS** provides buffering between arrival and processing. A temporary downstream
failure does not require the producer to retry directly against Snowflake.

**EventBridge** is useful when one event needs routing to several consumers or
different event types require different destinations. I would not add it merely
because it exists.

**Lambda** is appropriate for small stateless tasks such as validating metadata,
checking schemas, registering batches and dispatching work. I would not use one
Lambda invocation to perform a huge large-scale transformation.

**Snowflake** becomes the persistent analytical store. File-based ingestion could
use Snowpipe for near-real-time arrival or scheduled/bulk `COPY INTO` when a
batch cadence is sufficient.

**dbt/SQL** can express the same transformations demonstrated locally: technical
validation, event history, current state, management intervals, analytical facts
and reporting marts.

---

## 3. Suggested warehouse layers

```text
RAW
  raw_visit_events
  raw_properties
  raw_contractors
  raw_incidents
  raw_property_management

STAGING
  stg_visit_events
  stg_properties
  stg_contractors
  stg_incidents
  stg_property_management

INTERMEDIATE
  int_visit_event_history
  int_visit_current_state
  int_visit_quality_issues
  int_property_management_intervals

FACTS
  fact_visits
  fact_incidents

MARTS
  mart_company_week
  mart_company_period
  mart_property_week
  mart_contractor
```

The important idea is not the table prefix. It is the separation of grains and
responsibilities.

---

## 4. Incremental processing at scale

I would avoid rebuilding all millions of records for every update.

### Visit ingestion

Every incoming event keeps:

```text
event_id
visit_id
source_version
operation
updated_at
ingestion_time
source_file / message reference
```

`event_id` remains the idempotency key.

For each `visit_id`, only a strictly higher `source_version` can change current
state.

This preserves the assignment's core rule:

```text
arrival time does not decide the winner
source_version per visit decides the winner
```

### Current-state update

A warehouse `MERGE` (or equivalent incremental model) can:

1. ignore already-seen `event_id`s;
2. preserve unique stale events in history;
3. update current state only when the incoming version is greater;
4. retain delete tombstones;
5. permit a genuinely newer reactivation.

### Incremental marts

When records change, I would recalculate only impacted partitions where practical:

```text
affected visit
-> affected property
-> affected local reporting week
-> affected historical company
-> affected mart rows
```

Periodic full reconciliation remains useful as a safety check.

---

## 5. Recommended scaling approach

For datasets with millions of records, the recommended design is to preserve the
same data-quality and state-management rules while changing the execution model:

```text
immutable raw landing in S3
-> decoupled ingestion through SQS where useful
-> lightweight validation/orchestration with Lambda
-> incremental loading into Snowflake
-> append-only event history
-> idempotent processing by event_id
-> current-state precedence by source_version
-> incremental facts and marts
-> monitoring, security and automated deployment controls
```

The central design principle is:

> Scale the execution architecture without changing the business rules.

This avoids unnecessary full rebuilds while retaining replayability, auditability
and deterministic state management.

---

## 6. Batch versus streaming

I would choose the simplest model that meets the SLA.

### Batch / micro-batch

Use when:

- source exports arrive every few minutes/hours/day;
- reporting does not require second-level freshness;
- operational simplicity matters.

Example:

```text
S3 -> scheduled COPY/Snowpipe -> Snowflake -> dbt incremental models
```

### Streaming

Use when:

- events arrive continuously;
- users need near-real-time state;
- event volume/concurrency requires a long-lived stream.

Possible evolution:

```text
API/events -> Kinesis or MSK -> consumers -> S3/Snowflake
```

Millions of records alone would not make me introduce Kafka/MSK. The arrival
rate, latency target and operational requirements should justify that complexity.

---

## 7. Idempotency, replay and failure recovery

The local solution already demonstrates the pattern I would preserve.

### Idempotency

```text
event_id          -> unique delivery/event identity
visit_id          -> business entity identity
source_version    -> state precedence
```

Repeated `event_id`s do not create another logical event.

### Replay

Raw S3 objects are retained so a batch can be reprocessed.

A processing/control table records:

```text
batch_id
source path
checksum
status
attempt count
started/completed timestamps
input count
valid/rejected count
error
```

### Dead-letter handling

Messages that repeatedly fail move to an SQS dead-letter queue.

They are investigated and replayed after the underlying problem is corrected.

A failed event or batch should not silently disappear and should not advance a
successful checkpoint.

---

# Security

## 8. AWS access control

Use IAM roles and least-privilege policies rather than long-lived access keys.

Examples:

```text
ingestion role:
  write only to the required S3 raw prefix
  send only to the required queue

processing role:
  read the required S3 prefix
  receive/delete messages from the required SQS queue
  read only the required secret
  emit logs/metrics

CI deployment role:
  deploy only approved application/infrastructure resources
```

Production access should be separated from developer access.

Where practical:

```text
development account/environment
staging account/environment
production account/environment
```

Humans should not need broad administrator permissions to run normal data jobs.

---

## 9. Encryption and secrets

### At rest

- S3 encryption, with KMS where required by the security model.
- Snowflake encryption is handled by the platform; additional governance controls
  can be layered through roles/policies.

### In transit

Use TLS for service-to-service connections.

### Secrets

Do not put database credentials, private keys or API tokens in:

```text
source code
GitHub repository
Docker image
plain environment files committed to Git
```

Use a managed secret store such as AWS Secrets Manager.

For Snowflake, prefer a secure storage integration / identity-based integration
over embedding cloud storage credentials in SQL or application code.

---

## 10. S3 protection

For raw data buckets:

- Block Public Access;
- disable public ACL-style access;
- use least-privilege bucket/IAM policies;
- restrict access to the specific prefixes a role requires;
- enable versioning when source-object recovery is valuable;
- use lifecycle policies for retention/cost management;
- log/audit sensitive access.

The raw layer should be effectively append-only for normal pipeline roles.

---

## 11. Snowflake security

Use role-based access control.

Example role hierarchy:

```text
LOADER_ROLE
  insert/load RAW only

TRANSFORM_ROLE
  read RAW/STAGING
  write INTERMEDIATE/FACT/MART

REPORT_ROLE
  read MART only

DATA_ENGINEER_ROLE
  controlled engineering access

SECURITY_ADMIN
  security administration, separated from normal development
```

For sensitive columns, consider:

- masking policies;
- row-access policies;
- secure views;
- SSO/MFA;
- network/private connectivity controls where justified.

Reporting users should not require direct access to raw operational data.

---

# Monitoring and observability

## 12. Infrastructure monitoring

### SQS

Monitor:

```text
queue depth
age of oldest message
messages sent/received/deleted
DLQ message count
```

A growing oldest-message age is often a better freshness warning than queue size
alone.

### Lambda

Monitor:

```text
errors
duration
timeouts
throttles
concurrency
retries
DLQ/failure destinations
```

### S3 / ingestion

Monitor:

```text
files/events received
expected vs actual arrival frequency
file size / row count anomalies
duplicate files/checksum changes
```

### Snowflake

Monitor:

```text
load failures
load latency
query failures
task failures
warehouse queueing
query duration
credit/cost consumption
report/model freshness
```

---

## 13. Data-quality monitoring

The assignment's audit metrics become production data-quality metrics.

Examples:

```text
technical reject rate
duplicate-delivery rate
stale-version rate
business-quality issue rate
unknown property/contractor count
negative-duration count
management records without interval match
facts without a requested/local week
source-to-target row reconciliation
current-state count changes
```

Do not alert merely because a metric is non-zero. Alert on unexpected change or a
defined threshold.

Example:

```text
technical reject rate > 1%
DLQ > 0 for 10 minutes
oldest SQS message > 15 minutes
latest successful load > 30 minutes old
report freshness misses SLA
```

---

## 14. Logging and tracing

Use structured logs containing identifiers such as:

```text
batch_id
event_id
visit_id
source file/message id
pipeline run id
environment
```

Do not log secrets or unnecessary personal information.

For each pipeline run, I would want to trace:

```text
source arrival
-> ingestion
-> raw landing
-> validation
-> current state
-> fact/mart refresh
-> report/dashboard refresh
```

---

## 15. Alerting

CloudWatch alarms can route alerts through SNS or the organisation's incident
system.

Typical severities:

```text
P1 / critical
  pipeline stopped, large backlog, report SLA missed

P2 / high
  repeated load failures, significant data-quality spike

P3 / warning
  cost anomaly, slow query, moderate freshness degradation
```

Alerts should be actionable. A dashboard with fifty permanently-red metrics is
decorative wallpaper, not monitoring.

---

# CI/CD

## 16. CI and CD

**Continuous integration (CI)** validates that a proposed code or model change
still builds and passes automated checks. This repository demonstrates CI with
GitHub Actions.

**Continuous delivery/deployment (CD)** promotes the tested version through
controlled environments after CI succeeds.

A production promotion flow could be:

```text
feature branch
     |
     v
pull request
     |
     v
CI:
lint + tests + clean build + data-model checks
     |
     v
code review
     |
     v
merge to main
     |
     v
deploy to development
     |
     v
integration / smoke checks
     |
     v
deploy to staging
     |
     v
approval
     |
     v
deploy to production
```

For low-risk systems some gates may be automated. Data-model and schema changes
should still have controlled promotion and rollback planning.

---

## 17. Infrastructure as code

AWS and Snowflake infrastructure should be versioned rather than manually clicked
into existence where practical.

Possible tools:

```text
Terraform
AWS CDK / CloudFormation
Snowflake Terraform provider
```

CI can produce a plan/diff; CD applies the approved version to the target
environment.

Environment-specific configuration should be separated from code.

---

# Versioning

## 18. Four different meanings of version

"Versioning" in this project has several separate layers that should be treated independently.

### 1. Business-record version

Already demonstrated locally:

```text
visit_id = V100
source_version = 4
```

This decides which source state is current.

### 2. Event identity

```text
event_id
```

This prevents the same event from being logically applied twice.

### 3. Code / deployment version

Git commit SHA and release tag identify exactly which application/model code was
deployed.

Example:

```text
commit: a1b2c3d
release: v1.4.0
```

The deployed service/report should be traceable back to that version.

### 4. Schema / data-contract version

If an event payload changes, version the contract.

Example:

```text
schema_version = 2
```

Consumers can then:

- support both versions temporarily;
- migrate/backfill;
- reject unsupported future versions clearly.

Do not reinterpret an old payload differently without a controlled schema change.

---

## 19. Database/model migrations

Schema changes should travel with the code change.

Examples:

```text
new nullable column
-> deploy schema/model support
-> deploy producer
-> backfill if required

rename/remove column
-> first migrate consumers
-> stop old producer usage
-> remove only after compatibility window
```

For analytics models, dbt model changes/tests can be version-controlled in Git.

For application databases, a migration framework may be used where appropriate.

---

## 20. Rollback strategy

Rollback depends on what changed.

### Application/orchestration code

Deploy the previous tested Git artifact.

### Infrastructure

Reapply the previous reviewed infrastructure version when safe.

### Analytical models

Rebuild from the immutable raw layer using the previous model/code version.

### Bad source delivery

Do not edit history invisibly. Quarantine the bad delivery, correct the source or
produce a new version, then replay.

An immutable raw layer makes recovery significantly easier.

---

## 21. Deployment safety for data changes

For a risky model change:

```text
build new model/table version
-> run reconciliation against existing production result
-> compare row counts/key metrics
-> switch consumer/view
-> retain old model temporarily
-> remove after confidence window
```

This reduces deployment risk by allowing reconciliation before consumers are switched to the new model.

---

# Design conclusions

The proposed production design keeps the local project's correctness guarantees
while adding operational controls suitable for larger workloads:

- immutable raw storage for replay and audit;
- idempotent event processing;
- explicit version precedence for current state;
- incremental warehouse transformations;
- separation of analytical grains;
- least-privilege security;
- infrastructure and data-quality monitoring;
- controlled CI/CD and rollback;
- schema, infrastructure and deployment versioning.

The architecture should remain proportional to actual throughput, freshness and
operational requirements.

---

## 22. Avoiding unnecessary complexity

For datasets with millions of records, the design would **not automatically** introduce:

```text
Kafka
Spark
Kubernetes
multiple microservices
real-time streaming
```

unless throughput, latency or organisational needs justify them.

A simpler:

```text
S3 + SQS + Lambda + Snowflake + SQL/dbt
```

design can already provide strong scalability, replayability and operational
separation for this type of workload.

The architecture should grow only when throughput, latency, reliability or organisational requirements justify the added complexity.
