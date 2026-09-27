"""Alerts command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_resolve(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    params = runtime["_common_filter_params"](args)
    params.update(
        {
            "host": args.host,
            "ip": args.ip,
            "instance_name": args.instance_name,
            "database_name": args.database_name,
            "alert_id": args.alert_id,
            "limit": args.limit,
        }
    )
    return runtime["_request"]("GET", "/dba/resolve", params=params)


def cmd_context(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/dba/context",
        params={
            "alert_id": args.alert_id,
            "instance_id": args.instance_id,
            "database_id": args.database_id,
            "refresh_ai_context": args.refresh_ai_context,
            "stale_after_hours": args.stale_after_hours,
        },
    )


def cmd_alert_evidence(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        f"/dba/alerts/{args.alert_id}/evidence",
        params={"before_hours": args.before_hours, "after_hours": args.after_hours},
    )


def cmd_alerts(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Flat current-alert list (platform v3.32+).

    Prefer this over ``alerts-list``: the instance name/host/type are inlined and the
    triggering actual/threshold are lifted out of the evidence blob, so answering "what is
    alerting right now" is one call with no per-alert follow-up and no wading through the
    platform's internal ``_dac_*`` bookkeeping fields.
    """
    return runtime["_request"](
        "GET",
        "/dba/alerts",
        params={
            "status": args.status,
            "severity": args.severity,
            "instance_id": args.instance_id,
            "limit": args.limit,
        },
    )


def cmd_silence_report(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Why one instance might not be alerting — all five mechanisms, active or not.

    Checking two of the five and concluding "nothing is suppressed" is the failure this
    replaces; inactive mechanisms are listed precisely so the caller can tell it looked
    everywhere.
    """
    return runtime["_request"]("GET", "/dba/instances/{0}/silence-report".format(args.instance_id))


def cmd_alerts_list(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/alerts",
        params={
            "status": None if args.all_statuses else args.status,
            "severity": args.severity,
            "tenant_id": args.tenant_id,
            "instance_id": args.instance_id,
            "page": args.page,
            "page_size": args.page_size,
            "start_time": args.start_time,
            "end_time": args.end_time,
        },
    )


def cmd_freshness(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        f"/dba/instances/{args.instance_id}/freshness",
        params={"stale_after_hours": args.stale_after_hours},
    )


def cmd_timeline(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        f"/dba/instances/{args.instance_id}/timeline",
        params={"hours": args.hours, "limit": args.limit},
    )




def _add_alert_commands(sub, runtime: dict[str, Any]) -> None:
    """告警。"""
    alert_evidence = sub.add_parser("alert-evidence")
    alert_evidence.add_argument("--alert-id", type=int, required=True)
    alert_evidence.add_argument("--before-hours", type=int)
    alert_evidence.add_argument("--after-hours", type=int)
    alert_evidence.set_defaults(func=runtime["cmd_alert_evidence"])

    alerts_v2 = sub.add_parser(
        "alerts",
        help="Current alerts, flat, instance inlined (v3.32+; prefer over alerts-list)",
    )
    alerts_v2.add_argument("--status", default="active", choices=["active", "resolved", "all"])
    alerts_v2.add_argument("--severity", choices=["low", "medium", "high", "critical"],
                            help="Alert severity. Closed vocabulary — the response's own "
                                 "`counts` enumerates it, so a typo must not come back as a 0.")
    alerts_v2.add_argument("--instance-id", type=int)
    alerts_v2.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    alerts_v2.add_argument("--limit", type=runtime["_positive_int"], default=200)
    alerts_v2.set_defaults(func=runtime["cmd_alerts"])

    alerts = sub.add_parser("alerts-list")
    alerts.add_argument("--status", default="active")
    alerts.add_argument("--all-statuses", action="store_true")
    alerts.add_argument("--severity", choices=["low", "medium", "high", "critical"],
                            help="Alert severity. Closed vocabulary — the response's own "
                                 "`counts` enumerates it, so a typo must not come back as a 0.")
    alerts.add_argument("--tenant-id")
    alerts.add_argument("--instance-id", type=int)
    alerts.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    alerts.add_argument("--page", type=runtime["_positive_int"], default=1)
    alerts.add_argument("--page-size", type=runtime["_positive_int"], default=20)
    alerts.add_argument("--start-time")
    alerts.add_argument("--end-time")
    alerts.set_defaults(func=runtime["cmd_alerts_list"])

    silr = sub.add_parser(
        "silence-report",
        help="Why an instance might not be alerting: all 5 mechanisms (v3.32+)",
    )
    silr.add_argument("--instance-id", type=int)
    silr.set_defaults(_needs_instance=True)
    silr.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    silr.set_defaults(func=runtime["cmd_silence_report"])
