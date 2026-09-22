# Database business inference from table structure

Use this workflow when the user asks what business a database probably serves and no reliable business ownership fact is already available.

## Fetch the evidence

```bash
python scripts/dba_api_client.py business-inference-evidence \
  --instance-id <ID> \
  --database <DATABASE_OR_SCHEMA>
```

The command makes exactly one existing, allowlisted `table_inventory` probe call. It reads table names and table comments only. It does not read table contents, row counts, sizes, SQL text, contacts, instance names, or the stored `service_domain`, and performs no write.

Do not substitute `instance`, `databases-search`, or ownership endpoints before inference: their names and stored ownership fields leak the answer and turn the inference into a lookup. Use them only to resolve the caller's target before starting, or after producing a blind inference when the user explicitly asks for validation against the recorded value.

## Decide whether inference is allowed

- `evidence_status=unavailable`: stop. Report the probe's `note` and the `probe_unavailable` limitation.
- `evidence_status=insufficient`: stop. An empty table inventory is not proof that the database has no business purpose.
- `evidence_status=ready`: inference is allowed, subject to `signal_quality.confidence_ceiling`.
- `confidence_ceiling` is a hard maximum. Lower it whenever signals conflict or remain generic.
- `sample_scope=alphabetical_prefix` means the row limit was reached. Unseen tables may contradict the visible prefix; never call this a complete inventory.
- `no_table_comments` means only names are available. Several independently meaningful names may support a low or medium inference; opaque names must produce `insufficient_evidence`.

## Separate signals from noise

Strong evidence is a repeated, coherent business vocabulary across independent table families, especially when comments agree with names. Examples of independent signals are customer/contract/payment, employee/payroll/attendance, order/shipment/invoice, or fund/portfolio/trade. Three tables that are variants of the same base table count as one signal family.

The database name is context, not independent evidence. Do not raise confidence merely because its name resembles a product or department.

Generic infrastructure tables do not identify a business: users, roles, permissions, configuration, dictionaries, audit logs, jobs, schedulers, locks, migrations, Quartz, Flyway, XXL-JOB, ETL metadata, and temporary/work tables. A schema dominated by these may be a technical component; report that as a possible technical purpose, not as a business domain.

Never invent a company-specific product, department, owner, or system name unless the returned table names/comments explicitly support it. If two distinct domains are present, return alternatives or say the database appears shared.

## Output contract

Return a concise Chinese object or equivalent prose with these semantics:

```json
{
  "database": "the requested database/schema",
  "status": "inferred | insufficient_evidence | unavailable",
  "likely_business": "a cautious domain description, or null",
  "confidence": "high | medium | low | none",
  "evidence": [
    {
      "signal": "business signal",
      "tables": ["table names that support it"],
      "comments": ["relevant returned comments"]
    }
  ],
  "alternatives": ["credible competing interpretations"],
  "limitations": ["copy and explain material limitations from the evidence envelope"],
  "provenance": "inferred from live table names/comments; not a recorded ownership fact"
}
```

Rules:

- High confidence requires at least three independent, business-specific signal families, comments that agree with the names, a complete sample, and no material conflict.
- Medium confidence requires at least two independent signal families. It is the maximum for a truncated sample.
- Low confidence is appropriate for one coherent signal family or names-only evidence with multiple agreeing names.
- Otherwise return `insufficient_evidence` with `likely_business=null` and `confidence=none`.
- Cite 2–6 representative tables/comments. Do not dump the full inventory into the answer.
- Always label the conclusion as an inference. Never write it to `service_domain` or present it as platform-recorded ownership.
