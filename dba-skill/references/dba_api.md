# DBA API Reference — semantics and pitfalls

**This file deliberately does not list endpoints.** The platform exposes a self-describing
catalogue (`ai-endpoints`, 70+ read endpoints with summaries) plus a per-instance probe
catalogue (`probe-catalog`), and both stay current on their own. A hand-maintained endpoint
list cannot: this file previously covered 28 of 72 endpoints and none of the four added in
platform v3.32 — an agent sent here for "parameters and field semantics" was reading a stale
manual, which is exactly how wrong-but-confident answers get made.

So: **discover endpoints dynamically, read this file for what discovery cannot tell you.**

```text
ai-endpoints                 # what read endpoints exist, with descriptions
probe-catalog --instance-id  # what live drill-downs this instance supports
get <path> --param k=v       # call anything from the catalogue
```

Config and auth are in `SKILL.md`. Below is only the knowledge that is not derivable from a
schema.

---

## Never guess a query parameter name

Since platform v3.33.3 an unknown query parameter returns **422** with both `unknown_params`
and `accepted_params`. Read `accepted_params` rather than guessing again.

Before that fix the parameter was silently dropped and the request returned **200 with
unfiltered results** — `/dba/databases/search?database_name=payments` looked like it had
filtered and handed back the first 100 rows of the whole estate, so "who owns this database?"
was answered with an unrelated database's owner. If you are pointed at an older deployment,
verify a filter actually applied (responses echo the applied `filters`) before trusting it.

Free-text database search is **`q`**, not `database_name`.

## Metric names are not what you would guess — start from the catalogue

**There is no `cpu_usage`.** Guessing a metric name is the single easiest way to produce a
confident wrong answer here, because the name that sounds obvious does not exist and the real
one depends on *which collector wrote it*, not on the engine.

Get the vocabulary first: `get /dba/fleet/metric-names` returns every name the fleet actually
reports, with how many instances report it, which engine types, and units. `--param
contains=disk` narrows it. 125 names on the current estate.

CPU and memory are stored under **two vendor spellings for one signal** — Huawei/Prometheus
writes `host_cpu_usage_pct` / `host_memory_used_pct`, while Aliyun's CPU and memory arrive
from the *cost* collector as `cloud_cpu_usage_pct` / `cloud_mem_usage_pct`. Platform v3.39
aliases the pair, so asking for either now covers both (155 of 169 instances for CPU, versus
84 or 71 before). Each returned row carries `metric_name` showing which spelling answered, and
`resolved_metric_names` lists what was actually queried. `aliased_to` in the catalogue marks a
vendor spelling with the canonical name it stands in for.

`metric_name_known: false` means **the name is wrong**, not that collection stopped. The two
produce an identical empty result and call for opposite responses — fix the query, or go find
out why the fleet went quiet.

The remaining CPU gap is by design: host CPU/disk for self-managed instances belongs to
Zabbix, not this platform. Cloud RDS gets it from the cost collector.

## A fleet answer separates "fine" from "never measured"

`/dba/fleet/metrics` and `/dba/fleet/health` answer "which instances are above X" in one call
instead of one per instance. Both report the denominator, because a cross-instance result is
assembled from rows that exist:

- `coverage.instances_below_threshold` — measured, and fine.
- `coverage.instances_not_reporting` — **not measured at all**, each one named in
  `non_reporting_instances`.

Never answer "only 3 instances have a problem" from `items` alone. Read coverage first: three
hits out of 155 measured is a different statement from three out of 20.

## Inventory counts are about databases, not instances

`inventory-summary`'s `total_instances` counts instances **appearing in the matched database
rows** — it is not the fleet size. On the current estate it reads 70 while 169 instances are
eligible, because 98 have never had a database discovered. The `coverage` block carries
`instances_in_scope` / `instances_without_databases`, and every count's meaning is in the
response's own `definitions` block. Read those rather than assuming a count means what its
name suggests.

## Backups have two independent tracks

Local (RMAN/expdp) and offsite (NAS) are separate rows that can disagree. Production has had
an instance reading `determination=verified` / `status=success` locally while its offsite copy
had failed for two days and had never once succeeded.

- Single instance: `backups --instance-id N` — the response carries a `remote` summary; the
  offsite endpoint likewise carries `local`. Never answer from one track alone.
- Whole estate: `backups-coverage` (defaults to `at_risk`).
- `tracked: false` means **nobody ships this instance offsite**. That is not "fine".
- `not_applicable` means the platform is not responsible for this instance's backups (cloud
  RDS is the provider's job; a cluster component is backed up at cluster level). Counting
  those as failures once produced 161 at-risk instances out of 188 and buried the ~10 real
  ones.

## Metric values carry the platform's own doubts

Rows from `metrics/{id}/latest` may include `data_quality` (`drift` / `outlier` /
`null_value`, with severity). Roughly a third of the estate has at least one flagged metric.
Quote a flagged value only together with the flag. `data_quality: null` means *no open
finding* — the absence is information, not a missing field.

## Gaps are reported, never omitted

- `capacity-forecast --include-gaps` lists instances that could **not** be projected, with a
  reason. An instance missing from the list is not an instance without risk.
- Diagnostic probes return `available: false` plus `evidence_gaps`
  (`permission_denied` / `unsupported` / `error`) instead of an empty result.
- A `0` or an empty list that came from a failed collection is a gap. Check the gap fields
  before concluding "no problem found".

## "Not alerting" has five possible causes

Use `silence-report --instance-id N` instead of inferring. It always lists all five mechanisms
— alert silence, backup window, TiDB component roll-up, cloud-managed suppression, rules
disabled by override — **including the inactive ones**, so you can tell you checked
everywhere rather than concluding "nothing is suppressed" after looking at two.

## Capacity questions are not alert questions

`capacity-forecast` returns projections whether or not they are urgent enough to alert;
`would_alert` is a field, not a filter. A tablespace 200 days from full will never appear in
the alert list, and that lead time is the entire reason to ask.

## Freshness before conclusions

`freshness --instance-id N` says how current the data is. A healthy-looking instance whose
collection stopped yesterday is not healthy — it is unobserved. Alert timestamps are likewise
measured against the last report, not against now, for sources that push on a schedule.
