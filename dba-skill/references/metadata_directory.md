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
