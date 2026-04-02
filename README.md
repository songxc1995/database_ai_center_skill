# Database AI Center Skills

This repository contains two skills for external model platforms that can make outbound HTTP requests.

## Skills

- `database-ai-center/`
  - Primary alert-analysis skill
  - Reads live alerts, AI detail, and AI context from Database AI Center
  - Uses `GET /alerts`, `GET /alerts/{alert_id}/ai-detail`, and `GET /ai/context/{instance_id}`
  - Prefers `diagnosis_report`, `alert_evidence_items`, and `root_cause_candidates[*].evidence_items`
  - Emits and dispatches v1 evidence items instead of legacy string evidence
  - Produces the final Chinese diagnosis
  - Optionally enriches the diagnosis with `zabbix-readonly`

- `zabbix-readonly/`
  - Read-only Zabbix helper skill
  - Resolves a host by IP, optionally disambiguates by host name
  - Returns structured host-side performance evidence

## Runtime Config

Database AI Center:

```env
PROJECT_API_BASE_URL=http://your-database-ai-center/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_WEBHOOK_TARGET_ID=1
PROJECT_ALERT_ID=
PROJECT_ALERT_LIMIT=20
```

Zabbix:

```env
ZABBIX_BASE_URL=https://zabbix.example.com
ZABBIX_API_TOKEN=replace-with-real-token
ZABBIX_TIMEOUT_SECONDS=8
ZABBIX_VERIFY_TLS=true
```

## Recommended Usage

1. Trigger `database-ai-center` for alert analysis.
2. Let it fetch alerts via `GET /alerts?page=1&page_size=<n>` and select from the returned `items`.
3. Let it fetch `GET /alerts/{alert_id}/ai-detail` for normalized `diagnosis_report` and `alert_evidence_items`.
4. Let it fetch `GET /ai/context/{instance_id}` for `root_cause_candidates`, `explainability`, and `latest_metrics`.
5. Let it emit the final analysis as a v1 evidence-based diagnosis payload.
6. Let the model use `zabbix-readonly` only when host-side evidence is needed.
7. Keep the final diagnosis unified in `database-ai-center`.

## Handoff

- Self-contained external model handoff: `database-ai-center/HANDOFF.md`
- Main skill prompt and workflow: `database-ai-center/SKILL.md`

## Compatibility Notes

- This repository targets Database AI Center `v2.0.8+`.
- The primary skill depends on `GET /alerts`, `GET /alerts/{alert_id}/ai-detail`, and `GET /ai/context/{instance_id}`.
- It does not require direct calls to metrics-series endpoints or collection/discovery operations APIs.
- The skill emits v1 evidence items by default. The service may still normalize legacy evidence on dispatch, but this repository does not treat legacy evidence strings as the primary contract.
- Recent Database AI Center releases may describe Oracle pressure using long-running sessions rather than `slow_queries` semantics.
- This repository does not need an Oracle JDBC agent rebuild for prompt-only compatibility updates.
