# Persisted database metadata directory

Use these commands for database object inventory, cross-fleet object lookup, change history, and business-inference evidence. Read commands query the Database AI Center PostgreSQL snapshot only; they do not connect to the source database.

```bash
python scripts/dba_api_client.py metadata-coverage
python scripts/dba_api_client.py database-objects --database-id <ID> --all
python scripts/dba_api_client.py database-object-changes --database-id <ID> --all
python scripts/dba_api_client.py search-database-objects --name <NAME> --match exact --all
```

Always carry `status`, `completeness`, `collected_at`, `total`, and `truncated` into the answer. A partial or stale snapshot is evidence with a limitation, not a complete inventory. `never_collected`, `failed`, or an empty complete snapshot must not be paraphrased as “the database has no objects”.

The first phase stores tables, logical partition tables, views, materialized views, external tables, and Oracle synonyms. It deliberately does not store columns, keys, indexes, physical partition children, row counts, sizes, SQL text, or table contents. System databases and schemas are excluded unconditionally.

Cross-fleet search accepts exact or prefix matching. Search results describe current persisted objects in active, non-system databases; they are not a live catalogue lookup.

## Explicit admin refresh

Only use this when the user explicitly asks to refresh metadata and the active key has the `admin` role:

```bash
python scripts/dba_api_client.py refresh-database-metadata --database-id <ID>
python scripts/dba_api_client.py metadata-refresh-status --run-id <RUN_ID>
```

The POST only queues one predefined collector. It is not arbitrary SQL and cannot override server-side load freshness, CPU/connection thresholds, concurrency, statement timeout, total timeout, object budget, or the system-database exclusion. A queued response is not proof of completion; poll the returned run id and report `status`, `completeness`, counts, and any error.

## AI-client action order

For an AI client, use the approval workflow rather than the admin-only refresh endpoint:

```bash
python scripts/dba_api_client.py propose-metadata-refresh --database-id <ID> --reason "快照过期，需要重新采集" --evidence-ref /api/v2/dba/metadata/coverage
python scripts/dba_api_client.py action-order-status --order-id <ORDER_ID>
# A platform admin reviews the exact target and approves in the DBA 操作单 page.
python scripts/dba_api_client.py execute-action-order --order-id <ORDER_ID>
python scripts/dba_api_client.py action-order-status --order-id <ORDER_ID>
python scripts/dba_api_client.py verify-action-order --order-id <ORDER_ID>
```

The order is for one database and one use, expiring ten minutes after proposal. The same AI key that proposed it may execute only after independent admin approval. The server rechecks eligibility and cached source load before queueing; the worker rechecks load before opening a source connection. `verify-action-order` reads the persisted refresh result and current published snapshot before recording success. Call it only after `run_status` is terminal. A skipped or failed order needs a fresh proposal and approval; never silently retry. Future DingTalk conversation approval may replace the admin page, but it must update the same platform order through verified human identity, not through the AI client's API key.
