---
name: dba-skill
description: Query Database AI Center v2.19+ live DBA APIs for current/active alerts（现在有哪些告警）, database contacts（数据库联系人有哪些）, estate statistics, unused databases, ownership lookups, alert evidence, freshness, allowlisted diagnostics（含 live 探针下钻 probe-catalog/probe-run）, read-only PromQL against a TiDB cluster's Prometheus（prometheus-query：热点/黄金信号/per-store 流量, v2.63+）, ES/ELK database logs（elk-status/elk-coverage/elk-search：按主机+时间+级别+关键词检索 DB 日志, v2.24+）, cloud RDS data（cloud-rightsizing/cloud-cost-history：右规候选/成本, v2.74+）, backup evidence（backups：backup_method + determination「到底有没有备份」判定, v2.98+）, the knowledge base（历史根因/处理方案与运维手册检索, v2.32+）, and the self-describing read-endpoint catalog（ai-endpoints）for drilling into the long tail of model-reachable read APIs. Use when the agent needs DBA-ready facts or analysis from Database AI Center, optionally enriching host-side evidence with zabbix-readonly.
---

# DBA Skill

## Overview
Use this skill as the primary read-only DBA data and analysis workflow for Database AI Center `v2.19+` (server-side capabilities are discovered dynamically via `probe-catalog` and `ai-endpoints`, so newer platform releases are picked up without skill changes).

Prefer the `/api/v2/dba/*` endpoints. Do not use the legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}` chain as the main path.

Use the `alerts` helper command (`GET /dba/alerts`, v3.32+) for broad current alert inventory
questions: a flat list with instance name/host/type inlined and the triggering
`actual`/`threshold` lifted out of the evidence blob, so one call answers "what is alerting
right now". The older `alerts-list` hits the paginated UI endpoint and hands back the raw
evidence blob including the platform's internal `_dac_*` bookkeeping fields — prefer `alerts`.

Use `zabbix-readonly` only as supporting host evidence when Database AI Center data is insufficient for CPU, memory, disk, filesystem, load, or I/O questions.

## Reading the data honestly (v3.32+)

These are the places where a confident-sounding answer is most likely to be wrong. Read them
before summarising anything.

**Backups have two independent tracks.** Local (RMAN/expdp) and remote/offsite (NAS) are
separate rows that can disagree, and prod has had an instance reading `determination=verified`
/ `status=success` locally while its offsite copy had failed for two days and had never once
succeeded. Never answer "is this backed up?" from one track. Both
`GET /instances/{id}/backups` and `GET /instances/{id}/remote-backups` now carry a summary of
the other (`remote` / `local`). For the fleet-wide question use `GET /dba/backups/coverage`.
`tracked: false` means **nobody ships this instance offsite** — that is not the same as "fine",
and must never be reported as healthy.

**Metric values carry the platform's own doubts.** Each row from
`GET /metrics/{id}/latest` may include `data_quality` (`drift` / `outlier` / `null_value` with
severity). About a third of the fleet has at least one flagged metric. If a value you are about
to quote is flagged, say so; do not present it as a clean fact. `data_quality: null` means no
open finding — that absence is information, not a missing field.

**Gaps are reported, never omitted.** `GET /dba/capacity/forecast` returns instances it could
not project under `gaps` with a reason (pass `include_gaps=true`). Diagnostic probes return
`available: false` plus `evidence_gaps`. An empty list or a `0` that came from a failed
collection is a gap, not a healthy reading — check for the gap fields before concluding
"no problem found".

**"Not alerting" has five possible causes.** Use
`GET /dba/instances/{id}/silence-report` rather than inferring. It always lists all five
mechanisms (alert silence, backup window, TiDB component roll-up, cloud-managed suppression,
rules disabled by override), including the inactive ones, so you can tell you checked
everywhere instead of concluding "nothing is suppressed" after looking at two.

**Never guess a query parameter name.** From v3.33.3 the platform rejects unknown query
parameters with 422 and lists the accepted ones — before that it silently ignored them and
returned *unfiltered* results with a 200, so `?database_name=x` looked like it filtered and
handed back an unrelated database's owner. If a 422 names an unknown parameter, read the
`accepted_params` it returns rather than trying another guess. Free-text database search is
`--q` / `q=`, not `database_name`.

**Capacity questions are not alert questions.** `GET /dba/capacity/forecast` returns
projections whether or not they are urgent enough to alert; `would_alert` is a field, not a
filter. A trend 60 days out will never appear in the alert list — that lead time is the point.

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
python3 scripts/dba_api_client.py probe-run --instance-id 12 --probe full_join_statements
python3 scripts/dba_api_client.py prometheus-query --instance-id 8 --query 'count(pd_hotspot_status{type="hot_write_region_as_leader"} > 0)'
python3 scripts/dba_api_client.py kb-search --q "connection pool exhausted" --db-type oracle
python3 scripts/dba_api_client.py kb-search --keyword ORA-00060 --sort recency
python3 scripts/dba_api_client.py kb-incidents --root-cause-key "<root_cause_key from kb-search>"
python3 scripts/dba_api_client.py kb-doc-search --q "standby failover runbook"
python3 scripts/dba_api_client.py ai-endpoints
python3 scripts/dba_api_client.py get /dashboard/trends --param hours=6 --param bucket_minutes=15
python3 scripts/dba_api_client.py elk-status
python3 scripts/dba_api_client.py elk-coverage
python3 scripts/dba_api_client.py elk-search --host-ip 10.101.240.83 --levels ERROR,FATAL --start 2026-07-30T00:00:00Z --size 50
python3 scripts/dba_api_client.py cloud-rightsizing --window-days 30 --vendor huawei
python3 scripts/dba_api_client.py cloud-cost-history
python3 scripts/dba_api_client.py backups --instance-id 12
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
2. Run no-parameter snapshot probes first (`probe-run --probe slow_queries|active_sessions|blocking_chain|wait_events|locks|long_transactions|session_waits|resource_pressure`). `probe-catalog` is authoritative — the engine also exposes targeted probes, e.g. `full_join_statements` (MySQL: the per-digest SQL behind a `mysql_select_full_join_high` no-index-join alert), `error_statements` (TiDB: recently failing statements), and `db_error_log` (ELK-backed error log by host+window).
3. Drill down multi-round: take a `sql_id` from `slow_queries`/`active_sessions` → `probe-run --probe sql_plan --sql-id <id>` (check full scans / bad plans) → `probe-run --probe index_coverage --object-name <table>` and `probe-run --probe table_stats --object-name <table>` (are filter columns indexed? are optimizer stats stale?). Use `bind_values --sql-id <id>` for parameter-skew, `session_detail --session-id <id>` for one session.
4. Pass params only via `--sql-id` / `--session-id` / `--object-name`; never construct SQL. The server validates and binds them.

For TiDB cluster-level signals not visible to SQL probes (Database AI Center `v2.63+`), use `prometheus-query --instance-id <cluster-head> --query '<promql>'` — a **read-only** instant PromQL against the cluster's Prometheus (SSRF-guarded, GET-only won't reach it). Use it to check hotspots (`pd_hotspot_status{type="hot_write_region_as_leader"}` per store), per-store request/flow skew (`sum(rate(tikv_grpc_msg_duration_seconds_count[5m])) by (instance)`), leader/region balance, golden signals, and to verify a metric name/value before authoring a rule. Read-only — never a write query.

For database logs (Database AI Center `v2.24+`), use `elk-status` (are the ELK indices up?), `elk-coverage` (which instances are / are not shipping logs), and `elk-search --host-ip <ip> --levels ERROR,FATAL --start <iso> --end <iso>` to pull actual DB error-log lines as evidence. Search by the instance's host IP; narrow with `--levels` and a time window around the incident.

For cloud RDS (Database AI Center `v2.74+`), use `cloud-rightsizing` (per-instance peaks, downsize candidates, monthly cost + estimated saving; `--vendor aliyun|huawei`, `--window-days`) and `cloud-cost-history` (billing by month/year). These are cost/capacity facts — advisory, never an instruction to resize.

For "does this instance actually have a backup?" (Database AI Center `v2.98+`), use `backups --instance-id <id>`. Read the `determination` field, not the raw status — expdp dumps are invisible to RMAN so the status alone reads as a false "no backup":
- `verified` — evidence of a successful backup (rman: RMAN record; expdp/external: a reported or offsite record).
- `declared_no_evidence` — `backup_method` is marked (e.g. expdp) but the platform has received no backup evidence yet. **Not** "no backup" — it means the dump has not been reported in; the fix is to report it (`POST /instances/{id}/backups/report`) or wire the offsite/NAS pipeline.
- `not_tracked` — `backup_method=none` (deliberately excluded).
- `unknown` — no `backup_method` mark and no evidence; genuinely undeterminable until the instance is marked. Recommend marking `extra.backup_method` = rman / expdp / none.

For knowledge grounding (prior incidents + ops runbooks, Database AI Center `v2.32+`):

1. Before concluding a root cause, use `kb-search --q "<symptom in your own words>"` (or `--keyword ORA-xxxxx` for an exact code) to pull DBA-confirmed **symptom → root cause → remediation** history. Prefer `--db-type` to narrow by engine.
2. Use `kb-incidents --root-cause-key <key>` to open the raw incidents behind a returned entry (each links to a real past alert/diagnosis).
3. Use `kb-doc-search --q "<topic>"` to retrieve curated ops-runbook passages (handling steps, SOPs) relevant to the issue.
4. Treat knowledge-base hits as **prior evidence and references**, not ground truth: weigh them against the current live evidence, and say when your conclusion matches a past confirmed root cause. These endpoints are read-only and return an empty result (`available:false`) when the knowledge corpus is not enabled — degrade quietly, never block the answer.

### Analysis core group vs. the long tail
The commands above (`resolve`, `context`, `alert-evidence`, `alerts-list`, `classification`,
`inventory-summary`, `databases-search`, `databases-unused`, `ownership-scope`,
`directory-options`, `freshness`, `timeline`, `diagnostics-catalog`, `diagnostics-run`,
`probe-catalog`, `probe-run`, `prometheus-query`, `elk-status`, `elk-coverage`, `elk-search`,
`cloud-rightsizing`, `cloud-cost-history`, `backups`) are the **analysis core group** — the high-value read endpoints
you should reach for first. They cover most alert, ownership, inventory, and live-evidence
questions without needing to discover anything.

Database AI Center exposes many more read-only endpoints to `ai-client` keys beyond this core
group. When the core commands do not cover what you need:

1. Run `ai-endpoints` to fetch the **self-describing catalog** of every model-reachable read
   endpoint (path, methods, summary, description). It is derived from the live routes and their
   role grants, so it never drifts from actual permissions.
2. Pick a relevant `GET` path from the catalog and read it with
   `get <path> [--param key=value ...]`. Paths from the catalog come as full `/api/v2/...`; the
   helper strips the duplicate version prefix, so both `get /dashboard/trends` and
   `get /api/v2/dashboard/trends` work.
3. `get` is **GET-only** (read). It never issues writes; for live-DB probes keep using
   `probe-run` (rate-limited server-side), and for DBA checks keep using `diagnostics-run`.

Prefer a dedicated core command when one exists — it carries typed filters and safer defaults.
Use `ai-endpoints` + `get` for the long tail, not as a replacement for the core group.

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
- `GET /dba/alerts` (v3.32+) — flat alert list, instance inlined — helper: `alerts`
- `GET /dba/instances/{instance_id}/silence-report` (v3.32+) — why this instance might not be alerting — helper: `silence-report`
- `GET /dba/backups/coverage` (v3.32+) — fleet backup coverage across both tracks — helper: `backups-coverage` (defaults to `at_risk`)
- `GET /dba/capacity/forecast` (v3.32+) — projected exhaustion, alerting or not — helper: `capacity-forecast`
- `GET /instances/{instance_id}/diagnostics/catalog` (live probe catalog, `v2.19.0+`, used by `probe-catalog`)
- `POST /instances/{instance_id}/diagnostics/probe` (one live whitelisted probe incl. parameterized drill-down, `v2.19.0+`, used by `probe-run`)
- `POST /instances/{instance_id}/prometheus/query` (read-only instant PromQL against the instance's Prometheus, `v2.63+`, used by `prometheus-query`)
- `GET /knowledge/entries` (DBA-confirmed root-cause/remediation history, `v2.32+`, used by `kb-search`)
- `GET /knowledge/entries/{root_cause_key}/incidents` (raw incidents behind a root cause, used by `kb-incidents`)
- `GET /knowledge/documents/search` (semantic search over curated ops-runbook documents, `v2.39+`, used by `kb-doc-search`)
- `GET /elk/status` (ELK connectivity + which DB-log indices exist, `v2.24+`, used by `elk-status`)
- `GET /elk/coverage` (managed instances vs ELK log coverage, used by `elk-coverage`)
- `GET /elk/search` (search DB logs by host + time + level + keyword, used by `elk-search`)
- `GET /cloud-rds/rightsizing` (cloud RDS right-sizing: peaks, downsize candidates, cost + saving, `v2.74+`, used by `cloud-rightsizing`)
- `GET /cloud-rds/cost-history` (cloud RDS billing history, used by `cloud-cost-history`)
- `GET /instances/{instance_id}/backups` (backup status + `backup_method` + `determination` has-backup verdict, `v2.98+`, used by `backups`)
- `GET /ai-endpoints` (self-describing catalog of model-reachable read endpoints, `v2.47.0+`, used by `ai-endpoints`; drill into any listed path with `get`)

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
