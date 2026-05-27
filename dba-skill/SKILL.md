---
name: dba-skill
description: Query Database AI Center v2.0.21+ DBA APIs for database estate statistics, unused databases, ownership/contact/business lookups, alert evidence, freshness, and allowlisted diagnostics. Use when Codex needs DBA-ready facts or analysis from Database AI Center, optionally enriching host-side evidence with zabbix-readonly.
---

# DBA Skill

## Overview
Use this skill as the primary read-only DBA data and analysis workflow for Database AI Center `v2.0.21+`.

Prefer the `/api/v2/dba/*` endpoints. Do not use the legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}` chain as the main path.

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

## Helper Script
Prefer the bundled helper when local script execution is available:

```bash
python3 scripts/dba_api_client.py inventory-summary
python3 scripts/dba_api_client.py databases-search --business Payments --contact Alice
python3 scripts/dba_api_client.py context --alert-id 5
python3 scripts/dba_api_client.py diagnostics-catalog --instance-id 12
python3 scripts/dba_api_client.py diagnostics-run --instance-id 12 --checks database_sizes,storage
```

The helper prints JSON to stdout. It prints structured JSON errors to stderr and never prints the API key.

## Recommended Workflows
For asset, ownership, and governance questions:

1. Use `directory-options` for contact, department, or application autocomplete when the user gives partial names.
2. Use `databases-search` for “某联系人负责哪些库”, “某业务有哪些库”, “按部门/应用/联系人查询”.
3. Use `ownership-scope` for summary questions about one contact, business, or department.
4. Use `inventory-summary` for total database, inactive, unused, unowned, unassigned application, and stale discovery counts.
5. Use `databases-unused` for “哪些库不再使用”.

For alert and diagnosis questions:

1. Use `resolve` when the user gives host, IP, database name, application, contact, or alert id.
2. Use `alert-evidence` for alert-specific evidence.
3. Use `context` before producing DBA analysis.
4. Use `freshness` before making confidence claims.
5. Use `diagnostics-catalog` before any diagnostic run.
6. Use `diagnostics-run` only with catalog `check_id` values.

## Endpoint Map
See `references/dba_api.md` for parameters, field semantics, examples, and error handling.

Core endpoints:

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

## Safety Rules
- Use only returned API data and returned Zabbix data.
- Do not invent SQL, logs, topology events, metrics, owners, or contacts.
- Do not request or emit database passwords, API keys, encrypted secrets, usernames, or full connection strings.
- Do not run free-form SQL. Diagnostics must come from `diagnostics-catalog`.
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
