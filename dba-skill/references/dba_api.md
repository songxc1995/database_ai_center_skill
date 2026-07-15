# DBA API Reference

Use `PROJECT_API_BASE_URL` without a trailing slash, for example `http://host:8080/api/v2`.

All requests use:

```text
X-API-Key: <PROJECT_API_KEY>
```

## Recommended Flow

- Current alert list: `GET /alerts`
- Autocomplete: `GET /dba/directory/options`
- Instance topology classification: `GET /instances/classification`
- Search databases: `GET /dba/databases/search`
- Scope summary: `GET /dba/ownership/scope`
- Estate statistics: `GET /dba/inventory/summary`
- Unused database list: `GET /dba/databases/unused`
- Resolve context: `GET /dba/resolve`
- Evidence bundle: `GET /dba/context`
- Alert evidence: `GET /dba/alerts/{alert_id}/evidence`
- Freshness: `GET /dba/instances/{instance_id}/freshness`
- Diagnostics (DBA check catalog): `GET /dba/instances/{instance_id}/diagnostics/catalog` then `POST /dba/instances/{instance_id}/diagnostics/run`
- Live probes (multi-round drill-down, `v2.19.0+`): `GET /instances/{instance_id}/diagnostics/catalog` then `POST /instances/{instance_id}/diagnostics/probe`

## Query Endpoints

### `GET /alerts`

Parameters:

- `status=active`
- `severity`
- `tenant_id`
- `instance_id`
- `page=1`
- `page_size=20`
- `start_time`
- `end_time`

Use this read-only endpoint for broad “现在有哪些告警 / 当前告警 / active alerts” questions. Prefer `status=active` unless the user explicitly asks for all statuses.

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

### `GET /instances/classification`

Requires Database AI Center `v2.0.76+`.

Parameters:

- `type` (engine filter: `mysql` / `postgres` / `oracle` / `tidb` / `clickhouse`)
- `topology` (filter by topology kind, see below)
- `tenant_id`

Returns `{ generated_at, summary, items }`. Each item has `id`, `name`, `host`, `type` (engine), `role` (`primary` / `physical_standby` / `node` / ...), `topology`, `cluster_id`, `cluster_name`, `cloud_vendor`, `is_cloud_rds`, and `has_backup`. `topology` is one of `rac`, `dataguard`, `mysql_replication`, `mysql_group_replication`, `postgres_replication`, `tidb_cluster`, `clickhouse_cluster`, `replication`, `standalone`. `summary` aggregates `by_engine`, `by_topology`, `by_role`, `cloud_rds`, and `with_backup` counts.

Use for "哪些实例是 RAC / Data Guard / 单实例 / 主从", "哪些是云 RDS", and "哪些实例有备份" inventory questions. Note: a Data Guard primary or RAC not grouped into a cluster can appear as `standalone` (standbys are always identifiable); cloud RDS (`is_cloud_rds=true`) backups are not tracked by Database AI Center.

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

## Live diagnostic probes (multi-round drill-down)

Requires Database AI Center `v2.19.0+` and `AI_DIAGNOSTIC_PROBES_ENABLED=true` on the server. Roles: `admin` / `operator` / `ai-client`. These are the read-only, whitelisted probes the agentic AI pipeline uses — the client passes a probe **name** + bound params, never SQL; the fixed SQL stays server-side and output is redacted.

### `GET /instances/{instance_id}/diagnostics/catalog`

Lists the probes available for the instance's engine. Read this before drilling down.

```json
{
  "instance_id": 12,
  "db_type": "oracle",
  "probes": [
    {"probe": "active_sessions", "param": null, "requires_param": false, "supported": true},
    {"probe": "sql_plan", "param": "sql_id", "requires_param": true, "supported": true},
    {"probe": "table_stats", "param": "object_name", "requires_param": true, "supported": true}
  ]
}
```

### `POST /instances/{instance_id}/diagnostics/probe`

Runs ONE whitelisted probe, including the parameterized drill-down probes. Body:

```json
{ "probe": "sql_plan", "params": { "sql_id": "gm9ttamf39c40" } }
```

- `params` carries exactly one bound value: `sql_id` (sql_text / sql_plan / bind_values), `session_id` (session_detail), or `object_name` (index_coverage / table_stats). No-param snapshot probes omit `params`.
- Response: `{instance_id, db_type, probe, available, note, rows}`. `rows` are redacted; bind values are masked unless the server opts in.
- `409` when probes are disabled; unknown probe / unsupported db_type → `400`; a missing/invalid param degrades to `{available: false, note}` with HTTP `200`.

Typical multi-round drill-down: `slow_queries` → take a `sql_id` → `sql_plan` (check for full scans) → `index_coverage` + `table_stats` on the scanned table (are filter columns indexed? are stats stale?) → optionally `bind_values` for parameter-skew.

Probe names: `active_sessions`, `blocking_chain`, `slow_queries`, `wait_events`, `session_detail`, `sql_text`, `sql_plan`, `bind_values`, `index_coverage`, `table_stats`, `locks`, `long_transactions`, `session_waits`, `resource_pressure`. (`sql_text`/`sql_plan`/`bind_values` are Oracle; `index_coverage`/`table_stats` cover Postgres/MySQL/Oracle — use the catalog's `supported` flag.)

## Knowledge Base (prior incidents + ops runbooks, `v2.32+`)

Read-only (`viewer`+). Grounds a diagnosis in DBA-confirmed history and curated runbooks. Every endpoint degrades to a well-formed empty body when the RAG corpus / pgvector is absent (`available:false`); semantic search falls back to keyword/filters (`semantic_used:false` + a `warning`) rather than erroring.

### `GET /knowledge/entries` (helper: `kb-search`)

Confirmed root causes aggregated into entries. Query params:
- `q` — semantic query (embeds the text, widened recall).
- `keyword` — literal substring match (e.g. `ORA-00060`).
- `db_type` — `oracle` | `mysql` | `postgres` | `tidb` | `clickhouse`.
- `rule_id`, `sort` (`frequency` | `recency`), `limit`, `offset`.

Response: `{available, semantic_used, warning, total, entries:[{root_cause_key, root_cause, hit_count, db_types, rule_ids, first_seen_at, last_seen_at, representative_symptom, relevance}]}`. `hit_count` = how many times DBAs confirmed that root cause (a strength prior); `relevance` is `1 - cosine_distance` on semantic queries.

### `GET /knowledge/entries/{root_cause_key}/incidents` (helper: `kb-incidents`)

The raw incidents behind one entry. Query params: `db_type`, `rule_id`, `limit`. Response `{available, incidents:[{result_id, alert_event_id, db_type, rule_id, instance_id, instance_name, symptom_text, root_cause_text, remediation_text, created_at}]}`. Each `alert_event_id` maps to a real past diagnosis.

### `GET /knowledge/documents/search` (helper: `kb-doc-search`)

Semantic search over curated ops-runbook documents (`.md/.txt/.pdf/.docx` imported by DBAs). Query params: `q` (required), `limit`. Response `{available, semantic_used, warning, results:[{document_id, title, chunk_text, relevance}]}`.

Usage: treat hits as prior evidence/references, not ground truth — weigh against current live evidence and note when a conclusion matches a confirmed past root cause.

## Capabilities catalog (long-tail drill-in)

### `GET /ai-endpoints`

Self-describing catalog (`v2.47.0+`) of every model-reachable read endpoint an `ai-client` key
may call. Derived at runtime from the live routes and their `require_roles` grants, so it can
never drift from actual permissions. Response: a list of `{path, methods, summary, description}`
(v2 paths only, deduped). Used by the `ai-endpoints` helper command; no parameters.

Use it to discover read endpoints beyond the analysis core group, then drill in with `get`.

### `GET <any catalog path>` (via the `get` helper)

The `get <path> [--param key=value ...]` helper issues a plain **read-only GET** against any path
returned by the catalog. Paths come back as full `/api/v2/...`; the helper strips the duplicate
version prefix, so `get /dashboard/trends` and `get /api/v2/dashboard/trends` are equivalent.
`--param` is repeatable and maps to query parameters. It never issues writes; use `probe-run`
(rate-limited server-side) for live-DB probes and `diagnostics-run` for DBA checks.

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
