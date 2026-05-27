# DBA API Reference

Use `PROJECT_API_BASE_URL` without a trailing slash, for example `http://host:8080/api/v2`.

All requests use:

```text
X-API-Key: <PROJECT_API_KEY>
```

## Recommended Flow

- Autocomplete: `GET /dba/directory/options`
- Search databases: `GET /dba/databases/search`
- Scope summary: `GET /dba/ownership/scope`
- Estate statistics: `GET /dba/inventory/summary`
- Unused database list: `GET /dba/databases/unused`
- Resolve context: `GET /dba/resolve`
- Evidence bundle: `GET /dba/context`
- Alert evidence: `GET /dba/alerts/{alert_id}/evidence`
- Freshness: `GET /dba/instances/{instance_id}/freshness`
- Diagnostics: `GET /dba/instances/{instance_id}/diagnostics/catalog` then `POST /dba/instances/{instance_id}/diagnostics/run`

## Query Endpoints

### `GET /dba/directory/options`

Parameters:

- `type=contact|department|application`
- `search`
- `include_inactive=false`
- `limit=50`

Use this endpoint for contact, department, and application completion before applying exact filters.

### `GET /dba/databases/search`

Parameters:

- `tenant_id`
- `instance_type`
- `instance_id`
- `status`
- `include_inactive=false`
- `include_system_dbs=false`
- `is_in_use`
- `department`
- `service_domain`
- `business`
- `contact_person`
- `technical_contact`
- `contact`
- `contact_role=any|application|technical`
- `q`
- `limit=100`
- `offset=0`

`business` is an alias for `service_domain`. If both are supplied, they must match. `contact` searches both application and technical contacts unless `contact_role` narrows it.

### `GET /dba/ownership/scope`

Parameters:

- `contact`
- `contact_role=any|application|technical`
- `department`
- `service_domain`
- `business`
- `tenant_id`
- `include_inactive=false`
- `include_system_dbs=false`
- `stale_after_hours=72`

Use this for “某联系人/业务/部门的资产范围和风险概况”.

### `GET /dba/inventory/summary`

Parameters:

- `tenant_id`
- `instance_type`
- `instance_id`
- `department`
- `service_domain`
- `business`
- `contact_person`
- `technical_contact`
- `contact`
- `contact_role=any`
- `q`
- `include_system_dbs=false`
- `stale_after_hours=72`

Returns `counts`, `groups`, `definitions`, and `filters`.

### `GET /dba/databases/unused`

Parameters:

- `tenant_id`
- `instance_type`
- `instance_id`
- `department`
- `service_domain`
- `business`
- `contact_person`
- `technical_contact`
- `contact`
- `contact_role=any`
- `q`
- `include_inactive=false`
- `limit=100`

Default result is active non-system databases with `is_in_use=false`. With `include_inactive=true`, inactive rows are included with `reason=inactive`.

## Analysis Endpoints

### `GET /dba/resolve`

Parameters:

- `host`
- `ip`
- `instance_name`
- `database_name`
- `department`
- `service_domain`
- `business`
- `contact_person`
- `technical_contact`
- `contact`
- `contact_role=any`
- `q`
- `alert_id`
- `tenant_id`
- `limit=20`

Returns ids to feed into other DBA APIs.

### `GET /dba/context`

Parameters:

- `alert_id`
- `instance_id`
- `database_id`
- `refresh_ai_context=false`
- `stale_after_hours=72`

At least one selector is required. The response includes instance metadata, optional database metadata, cluster topology, health, metrics, active alerts, collection status, data quality issues, root-cause candidates, and freshness.

### `GET /dba/alerts/{alert_id}/evidence`

Parameters:

- `before_hours=6`
- `after_hours=1`

Returns alert detail, metric window, related alerts, collection jobs, AI results, and dispatch jobs.

### `GET /dba/instances/{instance_id}/timeline`

Parameters:

- `hours=24`
- `limit=100`

Returns collection, metric, health, alert, AI, dispatch, and database discovery events.

### `GET /dba/instances/{instance_id}/freshness`

Parameters:

- `stale_after_hours=72`

Returns latest collection, metric, health, database discovery timestamps, collection error, and `stale_evidence` labels.

## Diagnostics

### `GET /dba/instances/{instance_id}/diagnostics/catalog`

Read this before running diagnostics. Use returned `checks[].check_id` only.

### `POST /dba/instances/{instance_id}/diagnostics/run`

Body:

```json
{
  "checks": ["database_sizes", "storage"],
  "timeout_seconds": 10,
  "database_name": "payments"
}
```

Unknown checks return `400`. Arbitrary SQL fields return `422`. Live checks may return `skipped` until safe live collectors are enabled.

## Statistics Semantics

- `total_databases`: database rows matching filters.
- `active_databases`: rows with `status=active`.
- `inactive_databases`: rows with `status=inactive`, usually absent from latest discovery.
- `system_databases`: rows classified as system/internal.
- `business_databases`: non-system rows.
- `unused_databases`: active non-system rows with `is_in_use=false`.
- `unowned_databases`: non-system rows missing `contact_person` or `technical_contact`.
- `unassigned_application_databases`: non-system rows missing `service_domain`.
- `stale_discovery_databases`: non-system rows whose `last_refreshed_at` is older than `stale_after_hours`.
- `unused` is not `inactive`: unused still exists; inactive is no longer seen by discovery.

## Error Handling

- `400`: missing selector, invalid filter, unsupported diagnostic check.
- `401`: missing or invalid API key.
- `403`: role denied or tenant scope mismatch.
- `404`: requested object not visible to caller.
- `410`: tenant filter supplied while tenancy is disabled.
- `422`: invalid body, including arbitrary SQL fields.

Never include `PROJECT_API_KEY` in error text or final answers.
