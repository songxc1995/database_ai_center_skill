"""SQL digest trend and coverage commands; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any
import urllib.parse

_SQL_ENGINES = ("mysql", "postgres", "postgresql", "oracle", "tidb", "clickhouse", "sqlserver")
_SQL_COVERAGE_STATUSES = ("ready", "warming_up", "unavailable", "unsupported", "not_sampled")
_SQL_DIGEST_SORTS = ("total_time_ms", "mean_time_ms", "calls", "rows")


def cmd_sql_regressions(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Fleet-wide or single-instance SQL digest latency regressions vs baseline (v3.165+)."""
    return runtime["_request"](
        "GET",
        "/dba/sql-trends/regressions",
        params={
            "instance_type": args.instance_type,
            "instance_id": args.instance_id,
            "recent_hours": args.recent_hours,
            "baseline_days": args.baseline_days,
            "min_ratio": args.min_ratio,
            "min_mean_ms": args.min_mean_ms,
            "min_calls": args.min_calls,
            "min_baseline_samples": args.min_baseline_samples,
            "only_regressions": "false" if args.include_stable else None,
            "include_system_sql": "true" if args.include_system_sql else None,
            "limit": args.limit,
        },
    )


def cmd_sql_digests(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Top aggregated SQL digests for one instance within lookback hours (v3.165+)."""
    return runtime["_request"](
        "GET",
        f"/dba/sql-trends/instances/{args.instance_id}/digests",
        params={
            "hours": args.hours,
            "sort_by": args.sort,
            "limit": args.limit,
        },
    )


def cmd_sql_digest_series(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Time-series delta buckets for one SQL digest on an instance (v3.165+)."""
    did = str(args.digest_id or "").strip()
    if not did or did in {".", ".."} or ".." in did or "/" in did or "\\" in did:
        runtime["_fail"]("invalid_digest_id", "digest_id must be a non-empty single path segment.", exit_code=2)
    quoted = urllib.parse.quote(did, safe="")
    return runtime["_request"](
        "GET",
        f"/dba/sql-trends/instances/{args.instance_id}/digests/{quoted}/series",
        params={
            "hours": args.hours,
            "limit": args.limit,
        },
    )


def cmd_sql_trends_coverage(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Per-instance SQL digest collection coverage and unavailability breakdown (v3.166+)."""
    return runtime["_request"](
        "GET",
        "/dba/sql-trends/coverage",
        params={
            "instance_type": args.instance_type,
            "status": args.status,
            "search": args.search,
            "limit": args.limit,
            "offset": args.offset,
        },
    )


def _add_sql_trends_commands(sub, runtime: dict[str, Any]) -> None:
    """SQL 摘要趋势与退化。"""
    sreg = sub.add_parser(
        "sql-regressions",
        help="SQL digest latency regressions vs trailing baseline across fleet (v3.165+)",
    )
    sreg.add_argument("--instance-type", choices=list(_SQL_ENGINES))
    sreg.add_argument("--instance-id", type=runtime["_positive_int"])
    sreg.add_argument("--recent-hours", type=int)
    sreg.add_argument("--baseline-days", type=int)
    sreg.add_argument("--min-ratio", type=float)
    sreg.add_argument("--min-mean-ms", type=float)
    sreg.add_argument("--min-calls", type=int)
    sreg.add_argument(
        "--min-baseline-samples",
        type=runtime["_positive_int"],
        help="Minimum baseline 15m buckets required for candidate regressions (v3.167+ default 4).",
    )
    sreg.add_argument(
        "--include-stable",
        action="store_true",
        help="Include non-regressing active digests (passes only_regressions=false); useful during cold start.",
    )
    sreg.add_argument(
        "--include-system-sql",
        action="store_true",
        help="Include system/monitoring/internal maintenance SQL normally filtered by default (v3.167+, passes include_system_sql=true).",
    )
    sreg.add_argument("--limit", type=runtime["_positive_int"], default=100)
    sreg.set_defaults(func=runtime["cmd_sql_regressions"])

    sdig = sub.add_parser(
        "sql-digests",
        help="Top aggregated SQL digests for one instance over lookback hours (v3.165+)",
    )
    sdig.add_argument("--instance-id", type=int)
    sdig.set_defaults(_needs_instance=True)
    sdig.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    sdig.add_argument("--hours", type=int)
    sdig.add_argument("--sort", choices=list(_SQL_DIGEST_SORTS), help="Server-side sort field (maps to sort_by)")
    sdig.add_argument("--limit", type=runtime["_positive_int"], default=50)
    sdig.set_defaults(func=runtime["cmd_sql_digests"])

    sser = sub.add_parser(
        "sql-digest-series",
        help="15-minute delta buckets and plan_hash series for one SQL digest (v3.165+)",
    )
    sser.add_argument("--instance-id", type=int)
    sser.set_defaults(_needs_instance=True)
    sser.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    sser.add_argument("--digest-id", required=True)
    sser.add_argument("--hours", type=int)
    sser.add_argument("--limit", type=runtime["_positive_int"], default=500)
    sser.set_defaults(func=runtime["cmd_sql_digest_series"])

    scov = sub.add_parser(
        "sql-trends-coverage",
        help="Per-instance SQL digest collection coverage status and reason breakdown (v3.166+)",
    )
    scov.add_argument("--instance-type", choices=list(_SQL_ENGINES))
    scov.add_argument("--status", choices=list(_SQL_COVERAGE_STATUSES))
    scov.add_argument("--search")
    scov.add_argument("--limit", type=runtime["_positive_int"], default=200)
    scov.add_argument("--offset", type=int)
    scov.set_defaults(func=runtime["cmd_sql_trends_coverage"])
