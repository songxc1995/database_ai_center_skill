"""Core command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_whoami(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Identity, reach, limits and platform health — the four things to know before trusting
    an answer, in one call instead of three nobody thinks to make.

    A new user's first failure is almost never the question they asked: it is a key that
    expired, a role that cannot reach the endpoint, a rate limit they walked into, or a
    platform that is itself in a bad state. Each of those has its own endpoint; none of them
    is the one a person reaches for.
    """
    out: dict[str, Any] = {"credentials": runtime["_credential_provenance"]()}
    try:
        out["identity"] = runtime["_request"]("GET", "/auth/verify")
    except SystemExit:
        raise
    for label, path in (("platform", "/observability/version"),
                        ("self_check", "/observability/self-check")):
        try:
            payload = runtime["_request_once"]("GET", path)
        except Exception as exc:  # noqa: BLE001 - a partial answer beats no answer here
            out[label] = {"unavailable": runtime["_short_reason"](exc)}
            continue
        if label == "self_check" and isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k != "results"}
        out[label] = payload
    return out


def cmd_self_check(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Ask the platform whether it is currently contradicting itself.

    37 cross-subsystem invariants, each carrying the incident that motivated it. Worth running
    before reporting that anything is absent: "no alerts", "no backups", "no metrics" and "the
    platform cannot currently tell" are different answers, and only this endpoint distinguishes
    them. A violating check names the rows it caught, so it doubles as a lead.
    """
    payload = runtime["_request"]("GET", "/observability/self-check")
    if not getattr(args, "violations_only", False) or not isinstance(payload, dict):
        return payload
    return {
        **{k: v for k, v in payload.items() if k != "results"},
        "results": [r for r in (payload.get("results") or []) if r.get("violations")],
    }


def cmd_ai_endpoints(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    _ = args
    return runtime["_request"]("GET", "/ai-endpoints")


def cmd_get(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Read any catalogue path, tolerating the /dba prefix being present or absent.

    The catalogue mixes both: freshness lives under /dba/instances/{id}/freshness while the
    instance detail is the bare /instances/{id}. Getting it wrong returns a 404 that reads
    like "this instance does not exist" rather than "you used the wrong prefix", so the
    alternative is tried once before reporting failure.
    """
    path = runtime["_normalize_read_path"](args.path)
    params = runtime["_parse_kv_params"](args.param)
    try:
        return runtime["_request"]("GET", path, params=params)
    except SystemExit:
        if runtime["_LAST_HTTP_STATUS"] != 404:
            raise
        alternative = path[len("/dba"):] if path.startswith("/dba/") else "/dba" + path
        if alternative == path:
            raise
        try:
            payload = runtime["_request"]("GET", alternative, params=params)
        except SystemExit:
            raise SystemExit(1) from None
        runtime["sys"].stderr.write(
            runtime["json"].dumps(
                {"warning": "path_prefix_corrected", "requested": path, "used": alternative},
                ensure_ascii=False,
            )
            + "\n"
        )
        return payload




def _add_core_commands(sub, runtime: dict[str, Any]) -> None:
    """实例定位、目录与自检。"""
    instance = sub.add_parser(
        "instance",
        help="Everything about one instance from an id or an IP: detail, freshness, backups, "
             "database count/coverage, and active alerts — in one call.",
    )
    instance.add_argument("--instance-id", type=int)
    instance.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    instance.add_argument("--ip")
    instance.add_argument("--host")
    instance.set_defaults(func=runtime["cmd_instance"])

    onboarding = sub.add_parser(
        "onboarding-check",
        help="Is a newly onboarded instance actually wired up? Checks database inventory, "
             "declared backup method, ownership and ELK log coverage — metrics flowing says "
             "nothing about any of them.",
    )
    onboarding.add_argument("--instance-id", type=int)
    onboarding.set_defaults(_needs_instance=True)
    onboarding.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    onboarding.set_defaults(func=runtime["cmd_onboarding_check"])

    resolve = sub.add_parser("resolve")
    runtime["_common_filters"](resolve)
    resolve.add_argument("--host")
    resolve.add_argument("--ip")
    resolve.add_argument("--instance-name")
    resolve.add_argument("--database-name")
    resolve.add_argument("--alert-id", type=int)
    resolve.add_argument("--limit", type=int)
    resolve.set_defaults(func=runtime["cmd_resolve"])

    context = sub.add_parser("context")
    context.add_argument("--alert-id", type=int)
    context.add_argument("--instance-id", type=int)
    context.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    context.add_argument("--database-id", type=int)
    context.add_argument("--refresh-ai-context", action="store_true")
    context.add_argument("--stale-after-hours", type=int)
    context.set_defaults(func=runtime["cmd_context"])

    who = sub.add_parser(
        "whoami",
        help="Identity + expiry + rate limits + platform health — run this first when "
             "something is not working",
    )
    who.set_defaults(func=runtime["cmd_whoami"])

    selfchk = sub.add_parser(
        "self-check",
        help="Platform cross-subsystem invariants — run before reporting anything as absent",
    )
    selfchk.add_argument("--violations-only", action="store_true",
                         help="Return only the checks that are currently violated.")
    selfchk.set_defaults(func=runtime["cmd_self_check"])

    classification = sub.add_parser("classification")
    classification.add_argument("--limit", type=int)
    classification.add_argument("--type", choices=["mysql", "postgres", "oracle", "tidb", "clickhouse"])
    classification.add_argument(
        "--topology",
        help="Filter by topology kind: rac, dataguard, mysql_replication, mysql_group_replication, postgres_replication, tidb_cluster, clickhouse_cluster, replication, standalone",
    )
    classification.add_argument("--tenant-id")
    classification.set_defaults(func=runtime["cmd_classification"])

    ai_endpoints = sub.add_parser(
        "ai-endpoints",
        help="List the self-describing catalog of model-reachable (ai-client) read endpoints.",
    )
    ai_endpoints.set_defaults(func=runtime["cmd_ai_endpoints"])

    get_cmd = sub.add_parser(
        "get",
        help="GET any model-reachable read path from the ai-endpoints catalog (drill-in).",
    )
    get_cmd.add_argument("path", help="Read path, e.g. /dashboard/trends or /api/v2/topology")
    get_cmd.add_argument(
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help="Query parameter (repeatable), e.g. --param hours=6",
    )
    get_cmd.set_defaults(func=runtime["cmd_get"])
