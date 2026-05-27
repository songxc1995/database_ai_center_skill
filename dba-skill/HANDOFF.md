# DBA Skill External Model Handoff

This is the single-file handoff for external model teams integrating with Database AI Center `v2.0.21+`.

## Required Config

```env
PROJECT_API_BASE_URL=http://your-database-ai-center/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_TIMEOUT_SECONDS=15
PROJECT_STALE_AFTER_HOURS=72
```

Auth header:

```text
X-API-Key: <PROJECT_API_KEY>
```

## Primary Call Chains

For ownership and inventory questions:

```text
/dba/directory/options
-> /dba/databases/search
-> /dba/ownership/scope
-> /dba/inventory/summary
-> /dba/databases/unused
```

For alert and diagnostic analysis:

```text
/dba/resolve
-> /dba/alerts/{alert_id}/evidence
-> /dba/context
-> /dba/instances/{instance_id}/freshness
-> /dba/instances/{instance_id}/diagnostics/catalog
-> /dba/instances/{instance_id}/diagnostics/run
```

Diagnostics must use catalog `check_id` values. Free-form SQL is not supported.

## Recommended Helper

Use the bundled helper when a runtime can execute local Python:

```bash
python3 scripts/dba_api_client.py inventory-summary
python3 scripts/dba_api_client.py databases-search --contact Alice --contact-role technical
python3 scripts/dba_api_client.py ownership-scope --business Payments
python3 scripts/dba_api_client.py context --alert-id 5
python3 scripts/dba_api_client.py diagnostics-catalog --instance-id 12
python3 scripts/dba_api_client.py diagnostics-run --instance-id 12 --checks database_sizes,storage
```

The helper emits JSON and redacts `PROJECT_API_KEY` from errors.

## Core Endpoints

- `GET /dba/resolve`: resolve host, IP, instance, database, business, contact, or alert id to internal ids.
- `GET /dba/context`: return DBA-ready evidence for `alert_id`, `instance_id`, or `database_id`.
- `GET /dba/alerts/{alert_id}/evidence`: return alert, metric window, related alerts, AI results, jobs, and dispatch facts.
- `GET /dba/inventory/summary`: return total, active, inactive, system, business, unused, unowned, unassigned application, and stale discovery counts.
- `GET /dba/databases/search`: return paginated database rows filtered by contact, technical contact, department, business, usage, status, or free text.
- `GET /dba/databases/unused`: return active non-system `is_in_use=false` databases by default.
- `GET /dba/ownership/scope`: return contact/business/department scope summary and related applications, contacts, departments, instances.
- `GET /dba/directory/options`: return cached contact, department, or application options for autocomplete.
- `GET /dba/instances/{instance_id}/timeline`: return merged collection, health, alert, AI, dispatch, discovery timeline.
- `GET /dba/instances/{instance_id}/freshness`: return stale evidence labels and latest collection/discovery timestamps.
- `GET /dba/instances/{instance_id}/diagnostics/catalog`: return allowlisted diagnostic checks.
- `POST /dba/instances/{instance_id}/diagnostics/run`: run allowlisted diagnostics only.

## Key Semantics

- `business` is an alias for `service_domain`.
- `contact` matches `contact_person` and `technical_contact`.
- `contact_role=application` matches only `contact_person`.
- `contact_role=technical` matches only `technical_contact`.
- `unused` is not `inactive`: unused still exists but is marked `is_in_use=false`; inactive means discovery no longer sees it.
- `unowned` means missing application contact or technical contact.
- `stale_discovery` means `last_refreshed_at` is older than the selected stale threshold.

## Safety

- Do not expose passwords, tokens, encrypted secrets, usernames, or connection strings.
- Do not run free-form SQL.
- Do not invent facts when an API response is empty or stale.
- Use Zabbix only as supporting host evidence for OS-side pressure.
- Keep final answers in Chinese unless requested otherwise.

## Failure Handling

- `400`: invalid filters, missing selector, unsupported diagnostic check, or arbitrary SQL field.
- `401`: invalid API key.
- `403`: role denied or tenant scope mismatch.
- `404`: requested object is not visible.
- `410`: tenant filter supplied while tenancy is disabled.

When a call fails, keep any earlier evidence, mark the answer degraded, and include the failing endpoint and status code without leaking secrets.
