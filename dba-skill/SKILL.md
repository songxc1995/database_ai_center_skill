---
name: dba-skill
description: Live read-only DBA facts and analysis from Database AI Center — alerts, backups, ownership and contacts, inventory, cloud RDS cost, ELK logs, the knowledge base, and allowlisted live diagnostics against the database itself. Use when a question needs current data about the database estate rather than a guess; the server's own catalogues (ai-endpoints, probe-catalog) say what this particular deployment can do.
---

# DBA Skill

## Overview
Use this skill as the primary read-only DBA data and analysis workflow for Database AI Center `v2.19+` (server-side capabilities are discovered dynamically via `probe-catalog` and `ai-endpoints`, so newer platform releases are picked up without skill changes).

Version numbers written into this document are minimums, not a description of what is deployed —
they will drift, and chasing them here is how a document starts lying. `get
/observability/version` is the authoritative answer to "which release am I talking to", and
`ai-endpoints` / `probe-catalog` to "what can it do".

Prefer the `/api/v2/dba/*` endpoints. Do not use the legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}` chain as the main path.

Use the `alerts` helper command (`GET /dba/alerts`, v3.32+) for broad current alert inventory
questions: a flat list with instance name/host/type inlined and the triggering
`actual`/`threshold` lifted out of the evidence blob, so one call answers "what is alerting
right now". The older `alerts-list` hits the paginated UI endpoint and hands back the raw
evidence blob including the platform's internal `_dac_*` bookkeeping fields — prefer `alerts`.

If `zabbix-readonly` is installed, use it only as supporting host evidence when Database AI Center data is insufficient for CPU, memory, disk, filesystem, load, or I/O questions. It ships separately — do not assume it is there.

## Reading the data honestly (v3.32+)

**A page is not the answer.** Collections are paginated and a partial page looks exactly like
a complete one. Pass `--all` for any "which ones" question; the client also warns on stderr
when a page is partial. Prod: 2000 of 2072 rows returned, and the two instances being looked
for were in the missing 72.

**Timestamps are UTC. Host logs are UTC+8.** Convert before deciding two records are the same
event.

**`determination` is historical.** `verified` means a backup once succeeded — for "is it fine
now" read `recovery.latest_restore_point_at` and `sync_stale` / `sync_age_seconds`.

**`supported` in `probe-catalog` is not `available`.** Read `available` + `note` from the run.

**`--fields a,b,c` and `--format table`** cut a several-hundred-KB dump to the columns asked
about.

**One IP, one call:** `instance --ip <ip>` returns detail, freshness, backups, database
count/coverage and active alerts together. `onboarding-check --instance-id N` answers whether
a newly onboarded instance is actually wired up (databases, backup method, ownership, ELK).


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
python scripts/dba_api_client.py inventory-summary
python scripts/dba_api_client.py classification --type oracle
python scripts/dba_api_client.py directory-options --type contact --limit 20
python scripts/dba_api_client.py alerts-list --status active --page-size 20
python scripts/dba_api_client.py databases-search --business Payments --contact Alice
python scripts/dba_api_client.py context --alert-id 5
python scripts/dba_api_client.py diagnostics-catalog --instance-id 12
python scripts/dba_api_client.py diagnostics-run --instance-id 12 --checks database_sizes,storage
python scripts/dba_api_client.py probe-catalog --instance-id 12
python scripts/dba_api_client.py probe-run --instance-id 12 --probe slow_queries
python scripts/dba_api_client.py probe-run --instance-id 12 --probe sql_plan --sql-id gm9ttamf39c40
python scripts/dba_api_client.py probe-run --instance-id 12 --probe table_stats --object-name orders
python scripts/dba_api_client.py probe-run --instance-id 12 --probe full_join_statements
python scripts/dba_api_client.py prometheus-query --instance-id 8 --query 'count(pd_hotspot_status{type="hot_write_region_as_leader"} > 0)'
python scripts/dba_api_client.py kb-search --q "connection pool exhausted" --db-type oracle
python scripts/dba_api_client.py kb-search --keyword ORA-00060 --sort recency
python scripts/dba_api_client.py kb-incidents --root-cause-key "<root_cause_key from kb-search>"
python scripts/dba_api_client.py kb-doc-search --q "standby failover runbook"
python scripts/dba_api_client.py ai-endpoints
python scripts/dba_api_client.py get /dashboard/trends --param hours=6 --param bucket_minutes=15
python scripts/dba_api_client.py elk-status
python scripts/dba_api_client.py elk-coverage
python scripts/dba_api_client.py elk-search --host-ip 10.101.240.83 --levels ERROR,FATAL --start 2026-07-30T00:00:00Z --size 50
python scripts/dba_api_client.py cloud-rightsizing --window-days 30 --vendor huawei
python scripts/dba_api_client.py cloud-cost-history
python scripts/dba_api_client.py backups --instance-id 12
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
2. Run no-parameter snapshot probes first (`probe-run --probe slow_queries|active_sessions|blocking_chain|wait_events|locks|long_transactions|session_waits|resource_pressure`). `probe-catalog` is authoritative — the engine also exposes targeted probes, e.g. `full_join_statements` (MySQL: the per-digest SQL behind a `mysql_select_full_join_high` no-index-join alert), `error_statements` (TiDB: recently failing statements), and `db_error_log` (ELK-backed error log by host+window). **Oracle exposes a whole family the MySQL/TiDB examples above never hint at** — space (tablespace usage, per-owner and top segments, datafile autoextend), recoverability (FRA usage, archivelog status), and object health (invalid objects, failed scheduler jobs). Do not guess their names: `probe-catalog --instance-id N` returns the exact set for that engine with a `supported` flag on each.
3. Drill down multi-round: take a `sql_id` from `slow_queries`/`active_sessions` → `probe-run --probe sql_plan --sql-id <id>` (check full scans / bad plans) → `probe-run --probe index_coverage --object-name <table>` and `probe-run --probe table_stats --object-name <table>` (are filter columns indexed? are optimizer stats stale?). Use `bind_values --sql-id <id>` for parameter-skew, `session_detail --session-id <id>` for one session.
4. Pass params only via `--sql-id` / `--session-id` / `--object-name`; never construct SQL. The server validates and binds them.

For TiDB cluster-level signals not visible to SQL probes (Database AI Center `v2.63+`), use `prometheus-query --instance-id <cluster-head> --query '<promql>'` — a **read-only** instant PromQL against the cluster's Prometheus (SSRF-guarded, GET-only won't reach it). Use it to check hotspots (`pd_hotspot_status{type="hot_write_region_as_leader"}` per store), per-store request/flow skew (`sum(rate(tikv_grpc_msg_duration_seconds_count[5m])) by (instance)`), leader/region balance, golden signals, and to verify a metric name/value before authoring a rule. Read-only — never a write query.

For database logs (Database AI Center `v2.24+`), use `elk-status` (are the ELK indices up?), `elk-coverage` (which instances are / are not shipping logs), and `elk-search --host-ip <ip> --levels ERROR,FATAL --start <iso> --end <iso>` to pull actual DB error-log lines as evidence. Search by the instance's host IP; narrow with `--levels` and a time window around the incident.

For cloud RDS (Database AI Center `v2.74+`), use `cloud-rightsizing` (per-instance peaks, downsize candidates, monthly cost + estimated saving; `--vendor aliyun|huawei`, `--window-days`) and `cloud-cost-history` (billing by month/year). These are cost/capacity facts — advisory, never an instruction to resize.

For "does this instance actually have a backup?" (Database AI Center `v2.98+`), use `backups --instance-id <id>`. Read the `determination` field, not the raw status — expdp dumps are invisible to RMAN so the status alone reads as a false "no backup":
- `verified` — evidence of a successful backup (rman: RMAN record; expdp/external: a reported or offsite record).
- `declared_no_evidence` — `backup_method` is marked (e.g. expdp) but the platform has received no backup evidence yet. **Not** "no backup" — it means the dump has not been reported in; the fix is to report it (`POST /instances/{instance_id}/backups/report`) or wire the offsite/NAS pipeline. That is a write and an `ai-client` key cannot call it — say what needs doing rather than attempting it.
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
See `references/dba_api.md` for the semantics and pitfalls that a schema cannot express (metric-name spellings, two-track backups, data-quality flags, how gaps are reported, why an instance may not be alerting).

**Endpoints are discovered, not listed here.** `ai-endpoints` is derived from the live routes and their role grants, so it cannot drift; this file used to carry a hand-kept list and it drifted anyway — 36 entries against 84 reachable endpoints, missing every one added after platform v3.32. A list that lags is worse than no list, because it reads as complete. Use `ai-endpoints` for the surface and `get <path>` to call anything on it.

What follows is only the **command → endpoint** mapping, which discovery genuinely cannot give you: it says which questions already have a helper carrying typed filters and safer defaults. Anything not here is still reachable with `get`.

| Command | Endpoint |
| --- | --- |
| `resolve` | `GET /dba/resolve` |
| `context` | `GET /dba/context` |
| `alerts` | `GET /dba/alerts` |
| `alerts-list` | `GET /alerts` (compat path) |
| `alert-evidence` | `GET /dba/alerts/{alert_id}/evidence` |
| `silence-report` | `GET /dba/instances/{instance_id}/silence-report` |
| `capacity-forecast` | `GET /dba/capacity/forecast` |
| `backups` | `GET /instances/{instance_id}/backups` |
| `backups-coverage` | `GET /dba/backups/coverage` |
| `classification` | `GET /instances/classification` |
| `inventory-summary` | `GET /dba/inventory/summary` |
| `databases-search` | `GET /dba/databases/search` |
| `databases-unused` | `GET /dba/databases/unused` |
| `ownership-scope` | `GET /dba/ownership/scope` |
| `directory-options` | `GET /dba/directory/options` |
| `timeline` | `GET /dba/instances/{instance_id}/timeline` |
| `freshness` | `GET /dba/instances/{instance_id}/freshness` |
| `diagnostics-catalog` / `diagnostics-run` | `GET`/`POST /dba/instances/{instance_id}/diagnostics/...` |
| `probe-catalog` / `probe-run` | `GET`/`POST /instances/{instance_id}/diagnostics/...` (live, rate-limited) |
| `prometheus-query` | `POST /instances/{instance_id}/prometheus/query` (read-only PromQL) |
| `kb-search` / `kb-incidents` / `kb-doc-search` | `GET /knowledge/...` |
| `elk-status` / `elk-coverage` / `elk-search` | `GET /elk/...` |
| `cloud-rightsizing` / `cloud-cost-history` | `GET /cloud-rds/...` |
| `ai-endpoints` | `GET /ai-endpoints` (the catalogue itself) |
| `get <path>` | anything else in the catalogue |

**Response shapes.** A collection may arrive under `items`, `instances`, `events`, `probes`,
`entries`, `results` or `hits`, as a bare list, or as a single object. Guessing wrong is a
runtime crash (`'str' object has no attribute 'get'`), so branch on what is present rather than
assuming `items`. Note `probe-catalog` keys its entries by `probe`, not `name` or `id`.

Frequently useful paths that have **no** helper — reach them with `get`:
`/dba/fleet/metric-names` (metric vocabulary), `/dba/fleet/metrics` (one metric across the fleet), `/dba/fleet/health` (fleet health scores), `/observability/main-chain` (is collection→alerting→AI flowing), `/observability/version` (which release is answering — the only way to tell whether this document is stale), `/ai/observability` (is AI analysis succeeding), `/ai/rag/status` (is knowledge retrieval available), `/data-quality` (fleet-wide metric trustworthiness, paged: read `total` and `truncated`), `/clusters` (cluster membership — the direct way to find which primary a standby belongs to), `/ai/diagnosis-quality` (AI diagnosis accuracy), `/reports/inspection` (inspection reports), `/cloud-rds/downsizing-plans` (plans already acted on, not just candidates), `/alert-silences` and `/log-alert-rules` (silences and log-alert rules).

### Credentials, and running on Windows

The client reads `PROJECT_API_BASE_URL` / `PROJECT_API_KEY` from, in order of precedence:

1. the nearest `.env` walking up from the current directory,
2. the process environment,
3. `~/.dba-skill/config` — a per-user file in the same `KEY=value` format
   (`C:\Users\<name>\.dba-skill\config` on Windows).

The third exists for hosts launched from Finder/Explorer: their working directory is `/` or the
app bundle, so there is no `.env` above them, and a GUI-launched process inherits almost nothing
from the shell. Write that file once per machine and every host on it works — installing the
skill somewhere new needs no other step. It only fills in what nothing else supplied, so adding
it never changes a setup that already works.

```
# ~/.dba-skill/config      (chmod 600 on POSIX — the key is a credential)
PROJECT_API_BASE_URL=http://10.101.240.250:8080/api/v2
PROJECT_API_KEY=<your key>
```

If a call fails with 401/403, the error carries `per_key_source` — which file each value came
from. "The key was revoked" and "you are reading the wrong file" produce the same status code;
that field is how you tell them apart. It never contains the key itself.

**Interpreter:** the examples say `python`, which is what exists on Windows — the python.org
installer provides `python.exe` and `py.exe`, not `python3`. On a POSIX box where `python` is
missing or points at Python 2, use `python3`. The examples name the case that breaks silently:
a model copies the example, not the caveat next to it.

### "Does this instance have a backup?" has three vocabularies

Three endpoints answer versions of that question in three different shapes, and reading only
one of them is how a confident wrong answer gets produced.

| Field | Endpoint | Shape | What it actually means |
|---|---|---|---|
| `has_backup_run_records` | `classification` | bool | Only: does a backup **run record** exist. RMAN cannot see an expdp dump, so an instance declared `expdp` is `false` here while being backed up nightly. Never read this as "has a backup". |
| `determination` | `backups` | 4 states | The per-instance evidence verdict: `verified` / `declared_no_evidence` / `not_tracked` / `unknown`. This is the one that answers the question for a single instance. |
| `verdict` | `backups-coverage` | 8 states | The fleet-level judgement, which also folds in the offsite track and cluster coverage. `underlying_verdict` shows what the evidence said before suppression. |

Rule of thumb: **one instance → `determination`; the fleet → `verdict`; never `has_backup_run_records`.**

### `verdict` is a computed judgement, and it changes

The same query five hours apart returned different verdicts for instances 97, 59 and 69 — the
data had not changed, the judging logic had. That is normal (each change fixed a real
misreading), but it means a verdict is only true as of the `generated_at` in the same response.

Carry `generated_at` with any conclusion built on one, and re-run rather than reusing an
earlier answer. The same applies to `counts`: they describe the whole matched set, while
`items` is one page — check `truncated` before treating a list as complete.

### Is the platform itself telling the truth?

Before reporting that something is absent — no alerts, no backups, no metrics — consider that
the platform may simply not know. `get /observability/self-check` runs the cross-subsystem
invariants (37 of them) and returns `checks_violating` plus the offending rows; `get
/observability/main-chain` shows whether collection→alerting→AI is actually flowing, and `get
/observability/version` says which release is answering you. A finding of "nothing found" is
worth a lot less when these disagree.

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
