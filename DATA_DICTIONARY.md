# Source data dictionary

For the business meaning and relationships, start with
[Understanding the business and the data](BUSINESS_CONTEXT.md). This dictionary
describes the supplied source files; it is not a required analytics schema.
"Grain" below means what one row or record represents.

CSV files are UTF-8 with headers; empty fields mean null. JSONL has one independent
JSON record per line and deliberately includes malformed lines. Identifiers and
names are fictional. All timestamp values in valid records have explicit UTC offsets.

| File | Source grain and fields |
| --- | --- |
| data/properties.csv | One property: `property_id` key, `property_name`, IANA `timezone` |
| data/companies.csv | One managing company: `company_id` key, `company_name` |
| data/contractors.csv | One contractor: `contractor_id` key, `contractor_name` |
| data/property_management.csv | One observation: `observation_id` key, `property_id`, `company_id`, business `effective_at` |
| data/incidents.csv | One incident: `incident_id` key, `property_id`, `reported_at`, severity (`low`, `medium`, `high`) |
| data/report_weeks.csv | Local Monday `week_start` dates to include in reporting |
| data/visit_changes/*.jsonl | One exported change observation; fields described below |

## Visit changes

| Field | Meaning and technical validation |
| --- | --- |
| event_id | Nonempty string; global event identity |
| visit_id | Nonempty string; visit natural key |
| source_version | Positive integer identifying a visit's source state; booleans are invalid |
| operation | `upsert` or `delete` |
| updated_at | Required timezone-aware ISO 8601 source change timestamp; not the visit time, batch date or precedence key |
| property_id | Nonempty string required for upserts; references `properties.property_id` for a valid business record |
| contractor_id | Nonempty string required for upserts; references `contractors.contractor_id` for a valid business record |
| visitor_id | Nonempty string required for upserts; opaque person ID reused across their visits; no separate visitor-profile file |
| started_at | Required timezone-aware ISO 8601 timestamp for upserts |
| ended_at | Required field for upserts; either null or a timezone-aware ISO 8601 timestamp |

Deletes omit visit payload fields. Upserts contain the complete payload rather
than a partial patch. Repeated event IDs have identical content. Different event
IDs can repeat an older version with the same state; there are no conflicting
equal-version states in this dataset. Business-quality reference and duration
checks are separate from event-shape validation.

## Linking the files

| Source field | Related source | Meaning |
| --- | --- | --- |
| Management `property_id` | `properties.property_id` | The site being managed |
| Management `company_id` | `companies.company_id` | The managing organisation from `effective_at` |
| Incident `property_id` | `properties.property_id` | The site associated with the incident report |
| Visit upsert `property_id` | `properties.property_id` | The site attended |
| Visit upsert `contractor_id` | `contractors.contractor_id` | The organisation associated with this attendance |
| Visit change `visit_id` | Other changes with the same `visit_id` | Successive or repeated states of one business visit |

Visits and incidents obtain company attribution through property management at
their respective start/reported times. A join on property ID alone will encounter
multiple management observations; timing and unchanged states matter. There is
no direct visit-to-incident key or persistent visitor-to-contractor mapping.

`report_weeks.csv` is a list of requested local week labels, not another activity
feed. A property can have a reporting row even when no visits or incidents exist.

## Other source guarantees

Static table keys and incident IDs are unique. Property-management references are
valid; observations are unsorted and can describe unchanged states. There is one
initial manager per property before its first event, and no conflicting management
states at the same effective timestamp. Every incident has a valid property.
There are no incident changes or deletes in this project.

Property timezones do not change. A property has exactly one manager at a time;
contractor identity is independent of managing-company identity. A visitor can
visit several properties or contractors. Do not interpret visit count as unique
people or incident volume as a risk rate without explaining the denominator.

`manifest.json` supplies `batch_id`, repository-relative `file`, and `snapshot_date`.
The snapshot date labels the export batch, not the date of the visits. The files
never change for an existing batch ID in this project. Treat a newly delivered
batch as another input to process; raw data must remain committed unchanged.
