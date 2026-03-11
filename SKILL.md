# Database AI Center Skill

This file is a self-contained handoff for third-party model teams. If their model runtime can make HTTP requests, they only need this document plus a real project URL and API key.

## Required Config

Fill these two values:

- `PROJECT_API_BASE_URL`
- `PROJECT_API_KEY`

Optional values:

- `PROJECT_WEBHOOK_TARGET_ID`
- `PROJECT_ALERT_ID`
- `PROJECT_ALERT_LIMIT`

Example:

```env
PROJECT_API_BASE_URL=http://10.101.240.250:8080/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_WEBHOOK_TARGET_ID=1
PROJECT_ALERT_ID=
PROJECT_ALERT_LIMIT=20
```

## Runtime Requirements

The external model platform must support outbound HTTP calls. No extra model provider or second AI service is required.

## Skill Prompt

Use the following instruction as the skill/system prompt for the external model:

```text
Use the current model to analyze live database alerts from a Database AI Center deployment.

Read these values from environment variables or provided runtime config:
- PROJECT_API_BASE_URL
- PROJECT_API_KEY
- optional: PROJECT_WEBHOOK_TARGET_ID, PROJECT_ALERT_ID, PROJECT_ALERT_LIMIT

Workflow:
1. Call GET /alerts?limit=<n>.
2. If PROJECT_ALERT_ID is provided, choose that alert; otherwise choose the first alert.
3. Read instance_id from the chosen alert.
4. Call GET /ai/context/{instance_id}.
5. Produce a Chinese structured analysis with:
   - risk_level
   - summary
   - root_cause
   - evidence[]
   - actions[]
6. If the user asks to dispatch and PROJECT_WEBHOOK_TARGET_ID is configured, call POST /webhooks/{target_id}/dispatch-analysis.
7. Return a compact JSON result with alert, analysis, and dispatch.

Constraints:
- Use only returned API data.
- Do not invent SQL, logs, topology events, or metrics not present in the payload.
- Keep the analysis in Chinese.
- risk_level must be one of low|medium|high|critical.
- Prefer database-specific reasoning:
  - TiDB: PD, TiKV, Region/Leader distribution, hot Region, hotspot tables, scale events.
  - MySQL: connections, active threads, slow SQL, locks, replication, disk I/O.
  - PostgreSQL: sessions, locks, long transactions, checkpoints, replication lag.
  - Oracle: sessions, wait events, tablespace, archive logs, I/O pressure.
```

## API Contract

Auth header:

```text
X-API-Key: <PROJECT_API_KEY>
```

Base URL example:

```text
http://10.101.240.250:8080/api/v2
```

### 1. List Alerts

```http
GET /alerts?limit=20
```

Expected shape:

```json
[
  {
    "id": 1,
    "instance_id": 2,
    "rule_id": "tikv_region_imbalance_high",
    "severity": "critical",
    "status": "active",
    "message": "TiKV region distribution is imbalanced: 76.54",
    "evidence": {
      "metric": "tikv_region_imbalance_pct",
      "threshold": 70.0,
      "actual": 76.54
    }
  }
]
```

### 2. Get AI Context

```http
GET /ai/context/{instance_id}
```

Useful fields:

- `instance_profile.type`
- `instance_profile.name`
- `health_summary.total_score`
- `health_summary.grade`
- `active_alerts`

### 3. Dispatch Analysis

```http
POST /webhooks/{target_id}/dispatch-analysis
```

Request body:

```json
{
  "alert_id": 1,
  "transition": "triggered",
  "analysis": {
    "risk_level": "critical",
    "summary": "......",
    "root_cause": "......",
    "evidence": ["......"],
    "actions": ["......"]
  }
}
```

Expected response example:

```json
{
  "webhook_target_id": 1,
  "alert_event_id": 1,
  "target_url": "https://example.com/webhook",
  "status": "success",
  "http_status": 200,
  "latency_ms": 152,
  "detail": null
}
```

## Output Contract

Return this structure:

```json
{
  "alert": {
    "id": 1,
    "instance_id": 2,
    "rule_id": "tikv_region_imbalance_high",
    "severity": "critical",
    "status": "active",
    "message": "TiKV region distribution is imbalanced: 76.54"
  },
  "analysis": {
    "risk_level": "critical",
    "summary": "......",
    "root_cause": "......",
    "evidence": ["......"],
    "actions": ["......"]
  },
  "dispatch": null
}
```

Rules:

- `dispatch` is `null` when no webhook call is made.
- `summary` must mention the alert message.
- Include threshold and actual value when available.
- `evidence` should be short factual Chinese strings derived from API payloads.
- `actions` should contain 3 to 4 short Chinese action items.

## Failure Handling

- If `/alerts` returns an empty array, stop and report that there is no live alert to analyze.
- If the chosen alert has no valid `instance_id`, stop and report malformed alert payload.
- If `/ai/context/{instance_id}` fails, return the alert summary and explicitly note context retrieval failure.
- If dispatch fails, keep the analysis output and report dispatch failure separately.

## What To Send To Others

When handing this off, send only:

1. This file
2. `PROJECT_API_BASE_URL`
3. `PROJECT_API_KEY`

Optional:

4. `PROJECT_WEBHOOK_TARGET_ID`
5. A fixed `PROJECT_ALERT_ID` if they should analyze a specific alert

## Short Handoff Text

You can send this directly:

```text
请按文档中的 skill/system prompt 接入 Database AI Center。你只需要配置 PROJECT_API_BASE_URL 和 PROJECT_API_KEY，通过 /alerts 与 /ai/context/{instance_id} 拉取实时数据，并由当前模型直接输出中文结构化分析；如需投递，再调用 /webhooks/{target_id}/dispatch-analysis。
```
