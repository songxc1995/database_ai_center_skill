---
name: dba-skill
description: Query Database AI Center v2.0.21+ live DBA APIs for current/active alerts（现在有哪些告警）, database contacts（数据库联系人有哪些）, estate statistics, unused databases, ownership lookups, alert evidence, freshness, allowlisted diagnostics, and the knowledge base（历史根因/处理方案与运维手册检索, v2.32+）. Use when the agent needs DBA-ready facts or analysis from Database AI Center, optionally enriching host-side evidence with zabbix-readonly.
---

# DBA Skill

## Overview
Use this skill as the primary read-only DBA data and analysis workflow for Database AI Center `v2.0.21+`.

Prefer the `/api/v2/dba/*` endpoints. Do not use the legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}` chain as the main path.

Use the read-only `alerts-list` helper for broad current alert inventory questions until a dedicated `/dba/alerts` list endpoint exists.

Use `zabbix-readonly` only as supporting host evidence when Database AI Center data is insufficient for CPU, memory, disk, filesystem, load, or I/O questions.

## Required Config
Read these values from environment variables or runtime config:

- `PROJECT_API_BASE_URL`, for example `http://10.101.240.250:8080/api/v2`
- `PROJECT_API_KEY`

Optional:

- `PROJECT_TIMEOUT_SECONDS`, default `15`
- `PROJECT_STALE_AFTER_HOURS`, default `72`

Auth header:

```text
X-API-Key: <PROJECT_API_KEY>
```

The helper auto-loads allowlisted `PROJECT_*` values from the nearest `.env` in the current working directory or its parents. That nearest `.env` overrides inherited process environment variables so stale shell sessions cannot silently point at another deployment. Do not write API keys inline in shell commands or chat logs.

## Helper Script
Prefer the bundled helper when local script execution is available:

```bash
python3 scripts/dba_api_client.py inventory-summary
python3 scripts/dba_api_client.py classification --type oracle
python3 scripts/dba_api_client.py directory-options --type contact --limit 20
python3 scripts/dba_api_client.py alerts-list --status active --page-size 20
python3 scripts/dba_api_client.py databases-search --business Payments --contact Alice
python3 scripts/dba_api_client.py context --alert-id 5
python3 scripts/dba_api_client.py diagnostics-catalog --instance-id 12
python3 scripts/dba_api_client.py diagnostics-run --instance-id 12 --checks database_sizes,storage
python3 scripts/dba_api_client.py probe-catalog --instance-id 12
python3 scripts/dba_api_client.py probe-run --instance-id 12 --probe slow_queries
python3 scripts/dba_api_client.py probe-run --instance-id 12 --probe sql_plan --sql-id gm9ttamf39c40
python3 scripts/dba_api_client.py probe-run --instance-id 12 --probe table_stats --object-name orders
python3 scripts/dba_api_client.py kb-search --q "connection pool exhausted" --db-type oracle
python3 scripts/dba_api_client.py kb-search --keyword ORA-00060 --sort recency
python3 scripts/dba_api_client.py kb-incidents --root-cause-key "<root_cause_key from kb-search>"
python3 scripts/dba_api_client.py kb-doc-search --q "standby failover runbook"
```

The helper prints JSON to stdout. It prints structured JSON errors to stderr and never prints the API key.

## Recommended Workflows
For asset, ownership, and governance questions:

1. Use `directory-options` for contact, department, or application autocomplete when the user gives partial names.
2. Use `databases-search` for “某联系人负责哪些库”, “某业务有哪些库”, “按部门/应用/联系人查询”.
3. Use `ownership-scope` for summary questions about one contact, business, or department.
4. Use `inventory-summary` for total database, inactive, unused, unowned, unassigned application, and stale discovery counts.
5. Use `databases-unused` for “哪些库不再使用”.
6. Use `classification` for “哪些实例是 RAC / Data Guard / 单实例 / 主从”, “哪些是云 RDS”, and “哪些实例有备份” (topology + cloud + backup inventory).

For live list questions:

1. Use `directory-options --type contact` for “数据库联系人有哪些”, “联系人列表”, or “有哪些联系人”.
2. Use `alerts-list --status active` for “现在有哪些告警”, “当前告警”, “active alerts”, or “告警列表”.
3. Do not answer live list questions from documentation, sample data, repository search, or direct local metadata database queries.

For alert and diagnosis questions:

1. Use `alerts-list` first when the user asks which alerts currently exist.
2. Use `resolve` when the user gives host, IP, database name, application, contact, or alert id.
3. Use `alert-evidence` for alert-specific evidence.
4. Use `context` before producing DBA analysis.
5. Use `freshness` before making confidence claims.
6. Use `diagnostics-catalog` before any diagnostic run.
7. Use `diagnostics-run` only with catalog `check_id` values.

For live evidence drill-down (Database AI Center `v2.19.0+`, server `AI_DIAGNOSTIC_PROBES_ENABLED=true`):

1. Use `probe-catalog --instance-id N` to discover the probes the engine supports and which param each needs.
2. Run no-parameter snapshot probes first (`probe-run --probe slow_queries|active_sessions|blocking_chain|wait_events|locks|long_transactions|session_waits|resource_pressure`).
3. Drill down multi-round: take a `sql_id` from `slow_queries`/`active_sessions` → `probe-run --probe sql_plan --sql-id <id>` (check full scans / bad plans) → `probe-run --probe index_coverage --object-name <table>` and `probe-run --probe table_stats --object-name <table>` (are filter columns indexed? are optimizer stats stale?). Use `bind_values --sql-id <id>` for parameter-skew, `session_detail --session-id <id>` for one session.
4. Pass params only via `--sql-id` / `--session-id` / `--object-name`; never construct SQL. The server validates and binds them.

For knowledge grounding (prior incidents + ops runbooks, Database AI Center `v2.32+`):

1. Before concluding a root cause, use `kb-search --q "<symptom in your own words>"` (or `--keyword ORA-xxxxx` for an exact code) to pull DBA-confirmed **symptom → root cause → remediation** history. Prefer `--db-type` to narrow by engine.
2. Use `kb-incidents --root-cause-key <key>` to open the raw incidents behind a returned entry (each links to a real past alert/diagnosis).
3. Use `kb-doc-search --q "<topic>"` to retrieve curated ops-runbook passages (handling steps, SOPs) relevant to the issue.
4. Treat knowledge-base hits as **prior evidence and references**, not ground truth: weigh them against the current live evidence, and say when your conclusion matches a past confirmed root cause. These endpoints are read-only and return an empty result (`available:false`) when the knowledge corpus is not enabled — degrade quietly, never block the answer.

## Endpoint Map
See `references/dba_api.md` for parameters, field semantics, examples, and error handling.

Core endpoints:

- `GET /alerts` (read-only current alert list used by `alerts-list`)
- `GET /instances/classification` (instance topology / cloud-RDS / backup inventory, used by `classification`)
- `GET /dba/resolve`
- `GET /dba/context`
- `GET /dba/alerts/{alert_id}/evidence`
- `GET /dba/inventory/summary`
- `GET /dba/databases/search`
- `GET /dba/databases/unused`
- `GET /dba/ownership/scope`
- `GET /dba/directory/options`
- `GET /dba/instances/{instance_id}/timeline`
- `GET /dba/instances/{instance_id}/freshness`
- `GET /dba/instances/{instance_id}/diagnostics/catalog`
- `POST /dba/instances/{instance_id}/diagnostics/run`
- `GET /instances/{instance_id}/diagnostics/catalog` (live probe catalog, `v2.19.0+`, used by `probe-catalog`)
- `POST /instances/{instance_id}/diagnostics/probe` (one live whitelisted probe incl. parameterized drill-down, `v2.19.0+`, used by `probe-run`)
- `GET /knowledge/entries` (DBA-confirmed root-cause/remediation history, `v2.32+`, used by `kb-search`)
- `GET /knowledge/entries/{root_cause_key}/incidents` (raw incidents behind a root cause, used by `kb-incidents`)
- `GET /knowledge/documents/search` (semantic search over curated ops-runbook documents, `v2.39+`, used by `kb-doc-search`)

## Safety Rules
- Use only returned API data and returned Zabbix data.
- Do not invent SQL, logs, topology events, metrics, owners, or contacts.
- Do not request or emit database passwords, API keys, encrypted secrets, usernames, or full connection strings.
- Do not query the local metadata database or scrape repository docs as a substitute for live Database AI Center API data.
- Do not read or print `.env` directly. Use the helper so secrets stay out of chat logs.
- Do not place API keys in shell commands. Rely on the helper's nearest `.env` loading, environment variables, or an external secret manager wrapper.
- Do not run free-form SQL. Diagnostics come from `diagnostics-catalog` (DBA checks) or whitelisted probe names via `probe-catalog` / `probe-run` (`--sql-id` / `--session-id` / `--object-name` params only — never a SQL string).
- Do not mutate Database AI Center data; this skill is read-oriented except for allowlisted diagnostic execution.
- Keep final analysis in Chinese unless the user asks otherwise.
- Treat Zabbix as supporting evidence only, not a replacement for Database AI Center evidence.

## Field Semantics
- `business` is the natural-language alias for `service_domain`.
- `contact` matches both `contact_person` and `technical_contact` unless narrowed with `contact_role=application` or `contact_role=technical`.
- `unused_databases` means active non-system rows with `is_in_use=false`.
- `inactive_databases` means discovery no longer sees the database or it was marked inactive.
- `unowned_databases` means non-system rows missing application contact or technical contact.
- `stale_discovery_databases` means `last_refreshed_at` is older than `stale_after_hours`, default `72`.

## Output Guidance
For statistics or lookup questions, return:

```json
{
  "summary": "中文结论",
  "filters": {},
  "counts": {},
  "items": [],
  "data_quality": [],
  "freshness": {}
}
```

For alert or diagnostic analysis, return:

```json
{
  "scope": {},
  "analysis": {
    "summary": "中文摘要",
    "severity": "low|medium|high|critical",
    "root_cause": "中文根因",
    "evidence": [],
    "recommendations": []
  },
  "freshness": {},
  "diagnostics": []
}
```

Include caveats when evidence is stale, diagnostics are skipped, host evidence is unavailable, or the API returns partial data.

## Failure Handling
- If required config is missing, stop and say which variable is missing.
- If `resolve` returns no matches, say no matching instance/database/alert was found and show the filters used.
- If `context` or `alert-evidence` fails, return whatever earlier evidence is available and mark the result degraded.
- If freshness is stale, lower confidence and include the stale evidence labels.
- If `diagnostics-run` is not authorized or a check is skipped, keep the analysis and report the skipped check separately.
