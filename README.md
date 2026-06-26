# Database AI Center Skills

This repository contains skills for external model platforms that can make outbound HTTP requests.

## Skills

- `dba-skill/`
  - Replaces the former `database-ai-center/` alert-only skill.
  - Uses Database AI Center `v2.0.21+` `/api/v2/dba/*` APIs (plus the read-only `GET /alerts` and `GET /instances/classification` — the latter needs `v2.0.76+`).
  - Answers database estate statistics, unused database, inactive discovery, ownership, contact, department, business/application, current alerts, instance topology classification (RAC / Data Guard / replication / standalone / cloud RDS / has-backup), alert evidence, freshness, timeline, and allowlisted diagnostic questions.
  - **Live evidence drill-down (`v2.19.0+`):** runs the read-only, whitelisted diagnostic probes the agentic AI pipeline uses — `probe-catalog` / `probe-run` over `GET|POST /instances/{id}/diagnostics/{catalog,probe}` — for true multi-round root-cause analysis (slow_queries → sql_plan → index_coverage + table_stats → bind_values). Caller passes a probe **name** + bound params (`--sql-id` / `--session-id` / `--object-name`), never SQL; the fixed SQL stays server-side and output is redacted. Requires `AI_DIAGNOSTIC_PROBES_ENABLED=true` and role `ai-client` (or higher).
  - Includes `scripts/dba_api_client.py` to make common DBA API calls safely.
  - Optionally enriches analysis with `zabbix-readonly` for host-side evidence.

- `zabbix-readonly/`
  - Read-only Zabbix helper skill.
  - Resolves a host by IP, optionally disambiguates by host name.
  - Returns structured host-side performance evidence.

## Runtime Config

DBA Skill:

```env
PROJECT_API_BASE_URL=http://your-database-ai-center/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_TIMEOUT_SECONDS=15
PROJECT_STALE_AFTER_HOURS=72
```

Zabbix:

```env
ZABBIX_BASE_URL=https://zabbix.example.com
ZABBIX_API_TOKEN=replace-with-real-token
ZABBIX_TIMEOUT_SECONDS=8
ZABBIX_VERIFY_TLS=true
```

## Recommended Usage

1. Trigger `dba-skill` for DBA inventory, ownership, unused database, alert evidence, freshness, and diagnostic questions.
2. Use `dba-skill/scripts/dba_api_client.py` when local script execution is available.
3. Use `/dba/directory/options`, `/dba/databases/search`, `/dba/ownership/scope`, `/dba/inventory/summary`, and `/dba/databases/unused` for asset questions.
4. Use `/dba/resolve`, `/dba/alerts/{alert_id}/evidence`, `/dba/context`, `/dba/instances/{id}/freshness`, and diagnostics endpoints for alert analysis.
5. Use `zabbix-readonly` only when host-side CPU, memory, disk, filesystem, load, or I/O evidence is needed.

## Handoff

- External model handoff: `dba-skill/HANDOFF.md`
- Main skill prompt and workflow: `dba-skill/SKILL.md`
- DBA API reference: `dba-skill/references/dba_api.md`

## Compatibility Notes

- This repository targets Database AI Center `v2.0.21+`; the live diagnostic probe drill-down (`probe-catalog` / `probe-run`) requires `v2.19.0+`.
- The former `database-ai-center` skill name is retired in favor of `dba-skill`.
- The main path no longer depends on legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}`.
- Diagnostics never accepts free-form SQL: use DBA catalog `check_id` values (`diagnostics-run`) or whitelisted probe names + bound params (`probe-run`) only.
- Skill outputs and errors must not expose API keys, database credentials, tokens, encrypted secrets, usernames, or connection strings.
