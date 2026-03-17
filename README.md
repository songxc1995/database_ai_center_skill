# Database AI Center Skills

This repository contains two skills for external model platforms that can make outbound HTTP requests.

## Skills

- `database-ai-center/`
  - Primary alert-analysis skill
  - Reads live alerts and AI context from Database AI Center
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
2. Let the model use `zabbix-readonly` only when host-side evidence is needed.
3. Keep the final diagnosis unified in `database-ai-center`.
