# Database AI Center External Model Handoff

This file is the single-file handoff for external model teams integrating with Database AI Center `v2.0.8+`.

## Required Config

```env
PROJECT_API_BASE_URL=http://your-database-ai-center/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_WEBHOOK_TARGET_ID=1
PROJECT_ALERT_ID=
PROJECT_ALERT_LIMIT=20
```

Required:

- `PROJECT_API_BASE_URL`
- `PROJECT_API_KEY`

Optional:

- `PROJECT_WEBHOOK_TARGET_ID`
- `PROJECT_ALERT_ID`
- `PROJECT_ALERT_LIMIT`

## Runtime Requirements

- The model runtime must support outbound HTTP requests.
- No secondary AI service is required.
- This handoff assumes Database AI Center `v2.0.8+`.

## Call Sequence

1. Call `GET /alerts?page=1&page_size=<n>`.
2. Choose the alert from `items`.
   - If `PROJECT_ALERT_ID` is set, use that alert.
   - Otherwise use the first item.
3. Call `GET /alerts/{alert_id}/ai-detail`.
4. Read `instance_id` from the chosen alert.
5. Call `GET /ai/context/{instance_id}`.
6. Produce the final Chinese diagnosis from:
   - `ai-detail.diagnosis_report`
   - `ai-detail.alert_evidence_items`
   - `ai-context.root_cause_candidates[*].evidence_items`
   - `alert.evidence`
7. If the user asks to dispatch and `PROJECT_WEBHOOK_TARGET_ID` is set, call `POST /webhooks/{target_id}/dispatch-analysis`.

## Auth

```text
X-API-Key: <PROJECT_API_KEY>
```

## Primary APIs

### 1. List Alerts

```http
GET /alerts?page=1&page_size=20
```

Expected fields:

- `items[].id`
- `items[].instance_id`
- `items[].rule_id`
- `items[].severity`
- `items[].status`
- `items[].message`
- `items[].evidence`

### 2. Get AI Detail

```http
GET /alerts/{alert_id}/ai-detail
```

Expected fields:

- `evidence_schema_version`
- `alert`
- `alert_evidence_items`
- `ai_results`
- `ai_results[].diagnosis_report`
- `dispatch_jobs`

Important notes:

- `diagnosis_report` is the primary structured diagnosis payload.
- `diagnosis_report.evidence_schema_version` must be `v1`.
- `alert_evidence_items` is the normalized alert-side evidence list.

### 3. Get AI Context

```http
GET /ai/context/{instance_id}
```

Expected fields:

- `evidence_schema_version`
- `instance_profile`
- `summary`
- `health_summary`
- `active_alerts`
- `latest_metrics`
- `root_cause_candidates`
- `root_cause_candidates[].evidence_items`
- `explainability`

### 4. Dispatch Analysis

```http
POST /webhooks/{target_id}/dispatch-analysis
```

Request body:

```json
{
  "alert_id": 1,
  "transition": "triggered",
  "analysis": {
    "summary": "......",
    "severity": "critical",
    "root_cause": "......",
    "evidence_schema_version": "v1",
    "evidence": [
      {
        "source": "diagnosis",
        "code": "metric_code",
        "title": "......",
        "summary": "......",
        "metric_name": "metric_code",
        "value": 95,
        "context": {"threshold": 90}
      }
    ],
    "recommendations": [
      {"action": "......"}
    ]
  }
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
    "summary": "......",
    "severity": "critical",
    "root_cause": "......",
    "evidence_schema_version": "v1",
    "evidence": [
      {
        "source": "diagnosis",
        "code": "metric_code",
        "title": "......",
        "summary": "......",
        "metric_name": "metric_code",
        "value": 95,
        "context": {"threshold": 90}
      }
    ],
    "recommendations": [
      {"action": "......"}
    ]
  },
  "dispatch": null
}
```

## Evidence Priority

Use evidence in this order:

1. `ai-detail.ai_results[].diagnosis_report`
2. `ai-detail.alert_evidence_items`
3. `ai-context.root_cause_candidates[].evidence_items`
4. `alert.evidence`

Zabbix is supporting evidence only. It must not replace database-side primary evidence.

## Failure Handling

- If `/alerts` returns `items: []`, stop and report that there is no live alert to analyze.
- If the chosen alert has no valid `instance_id`, stop and report malformed alert payload.
- If `/alerts/{alert_id}/ai-detail` fails, continue with `/ai/context/{instance_id}` only and explicitly mark the result as degraded.
- If `/ai/context/{instance_id}` fails, return the alert plus any available AI detail and explicitly note context retrieval failure.
- If both `ai-detail` and `ai-context` are unavailable, stop and report insufficient structured evidence.
- If dispatch fails, keep the analysis output and report dispatch failure separately.
