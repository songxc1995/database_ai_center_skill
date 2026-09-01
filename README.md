# Database AI Center Skills

This repository contains skills for external model platforms that can make outbound HTTP requests.

## Skills

- `dba-skill/`
  - Replaces the former `database-ai-center/` alert-only skill.
  - Uses Database AI Center `v2.0.21+` `/api/v2/dba/*` APIs (plus the read-only `GET /alerts` and `GET /instances/classification` — the latter needs `v2.0.76+`).
  - Answers database estate statistics, unused database, inactive discovery, ownership, contact, department, business/application, current alerts, instance topology classification (RAC / Data Guard / replication / standalone / cloud RDS / has-backup), alert evidence, freshness, timeline, and allowlisted diagnostic questions.
  - **Live evidence drill-down (`v2.19.0+`):** runs the read-only, whitelisted diagnostic probes the agentic AI pipeline uses — `probe-catalog` / `probe-run` over `GET|POST /instances/{id}/diagnostics/{catalog,probe}` — for true multi-round root-cause analysis (slow_queries → sql_plan → index_coverage + table_stats → bind_values). Caller passes a probe **name** + bound params (`--sql-id` / `--session-id` / `--object-name`), never SQL; the fixed SQL stays server-side and output is redacted. Requires `AI_DIAGNOSTIC_PROBES_ENABLED=true` and role `ai-client` (or higher).
  - **Knowledge base grounding (`v2.32+`):** `kb-search` / `kb-incidents` / `kb-doc-search` over `GET /knowledge/entries`, `.../incidents`, and `GET /knowledge/documents/search` — pull DBA-confirmed symptom→root-cause→remediation history and curated ops-runbook passages to ground a diagnosis in prior incidents. Read-only (`viewer`+); degrades to empty when the RAG corpus is absent.
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

- Main skill prompt and workflow: `dba-skill/SKILL.md`
- DBA API semantics and pitfalls: `dba-skill/references/dba_api.md`
- **Third-party model teams** (no repo access, HTTP only): `docs/external-skill-quickstart.md`
  in the platform repo — self-contained, and it lives next to the API it describes.

> There is deliberately **no separate handoff file here.** There used to be
> (`dba-skill/HANDOFF.md`), and it had no audience of its own: agents loading the skill read
> `SKILL.md`, third parties read the platform repo's quickstart. What it did have was drift —
> a copy of an API description, one repo away from the API, cannot be updated in the same
> commit as the change it describes. By the time it was removed it still claimed to target
> `v2.0.21+` (the platform had shipped 40+ releases since) and listed 13 endpoints out of 84.
> If a single-file deliverable is needed again, extend the platform repo's quickstart rather
> than re-creating a second copy here.

## Compatibility Notes

- This repository targets Database AI Center `v2.0.21+` and degrades cleanly on older servers (404/empty) — every capability below is optional, not required:
  - live diagnostic probe drill-down (`probe-catalog` / `probe-run`): `v2.19.0+`
  - knowledge base (`kb-search` / `kb-incidents` / `kb-doc-search`): `v2.32+`, document search `v2.39+`
  - self-describing read-endpoint catalog (`ai-endpoints`): `v2.47.0+`
  - unknown query parameters answered with `422` + `accepted_params` instead of being silently dropped: `v3.33.3+` — **below this, verify a filter actually applied before trusting the rows**
  - fleet-wide metric/health queries and `metric-names`: `v3.38.0+`, vendor metric-name aliasing `v3.39.0+`
  - staleness measured against `last_sync_at` rather than `now`: `v3.41.0+`
  - `database_inventory_coverage` on an instance, and `instances_covered_by_cluster_owner` in inventory coverage: `v3.46.0+`
  - `routing_skip` in `ai/observability`'s audit trail (why a notification was not sent): `v3.47.0+`
- The former `database-ai-center` skill name is retired in favor of `dba-skill`.
- The main path no longer depends on legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}`.
- Diagnostics never accepts free-form SQL: use DBA catalog `check_id` values (`diagnostics-run`) or whitelisted probe names + bound params (`probe-run`) only.
- Skill outputs and errors must not expose API keys, database credentials, tokens, encrypted secrets, usernames, or connection strings.
