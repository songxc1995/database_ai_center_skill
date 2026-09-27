"""Elk command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_elk_status(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", "/elk/status")


def cmd_elk_coverage(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", "/elk/coverage")


def cmd_elk_search(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/elk/search",
        params={
            "host_ip": args.host_ip,
            "host_name": args.host_name,
            "start": args.start,
            "end": args.end,
            "levels": args.levels,
            "query_string": args.query_string,
            "index": args.index,
            "size": args.size,
        },
    )




def _add_elk_commands(sub, runtime: dict[str, Any]) -> None:
    """日志(ELK)。"""
    elk_status = sub.add_parser(
        "elk-status", help="ELK connectivity + which DB-log indices exist (v2.24+)"
    )
    elk_status.set_defaults(func=runtime["cmd_elk_status"])

    elk_coverage = sub.add_parser(
        "elk-coverage", help="Managed instances vs ELK log coverage (who is / isn't shipping logs)"
    )
    elk_coverage.set_defaults(func=runtime["cmd_elk_coverage"])

    elk_search = sub.add_parser(
        "elk-search", help="Search DB logs by host + time window + level + keyword"
    )
    elk_search.add_argument("--host-ip", help="DB host IP")
    elk_search.add_argument("--host-name", help="DB hostname fallback")
    elk_search.add_argument("--start", help="Start time (ISO 8601)")
    elk_search.add_argument("--end", help="End time (ISO 8601)")
    elk_search.add_argument("--levels", help="Comma-separated log levels, e.g. ERROR,FATAL")
    elk_search.add_argument("--query-string", help="ES query_string filter")
    elk_search.add_argument("--index", help="Index pattern override")
    elk_search.add_argument("--size", type=int, default=200, help="Max rows (1-1000)")
    elk_search.set_defaults(func=runtime["cmd_elk_search"])
