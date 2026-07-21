#!/usr/bin/env python3
"""Small read-oriented client for Database AI Center DBA APIs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


READ_TIMEOUT_DEFAULT = 15
ENV_FILE_NAMES = (".env",)
ENV_ALLOWLIST = {
    "PROJECT_API_BASE_URL",
    "PROJECT_API_KEY",
    "PROJECT_TIMEOUT_SECONDS",
    "PROJECT_STALE_AFTER_HOURS",
}
_ENV_FILES_LOADED = False


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_files() -> None:
    global _ENV_FILES_LOADED
    if _ENV_FILES_LOADED:
        return
    _ENV_FILES_LOADED = True

    try:
        search_roots = (Path.cwd().resolve(), *Path.cwd().resolve().parents)
    except OSError:
        return

    for directory in search_roots:
        for filename in ENV_FILE_NAMES:
            path = directory / filename
            if not path.is_file():
                continue
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                key, separator, value = line.partition("=")
                key = key.strip()
                if separator != "=" or key not in ENV_ALLOWLIST:
                    continue
                os.environ[key] = _unquote_env_value(value)
            return


def _env(name: str, default: str | None = None) -> str | None:
    _load_env_files()
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _base_url() -> str:
    value = _env("PROJECT_API_BASE_URL")
    if not value:
        _fail("missing_config", "PROJECT_API_BASE_URL is required", exit_code=2)
    return value.rstrip("/")


def _api_key() -> str:
    value = _env("PROJECT_API_KEY")
    if not value:
        _fail("missing_config", "PROJECT_API_KEY is required", exit_code=2)
    return value


def _timeout() -> float:
    raw = _env("PROJECT_TIMEOUT_SECONDS", str(READ_TIMEOUT_DEFAULT))
    try:
        value = float(raw or READ_TIMEOUT_DEFAULT)
    except ValueError:
        _fail("invalid_config", "PROJECT_TIMEOUT_SECONDS must be numeric", exit_code=2)
    if value <= 0:
        _fail("invalid_config", "PROJECT_TIMEOUT_SECONDS must be greater than 0", exit_code=2)
    return value


def _redact(value: str) -> str:
    key = os.environ.get("PROJECT_API_KEY")
    if key:
        value = value.replace(key, "<redacted>")
    return value


def _fail(error: str, message: str, *, exit_code: int = 1, **extra: Any) -> None:
    payload = {"error": error, "message": _redact(message), **extra}
    sys.stderr.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    raise SystemExit(exit_code)


def _print_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _add_if(params: dict[str, str], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and value == "":
        return
    if isinstance(value, bool):
        if value:
            params[key] = _bool(value)
        return
    params[key] = str(value)


def _clean_params(values: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in values.items():
        _add_if(params, key, value)
    return params


def _checks(raw: str) -> list[str]:
    checks = [part.strip() for part in raw.split(",") if part.strip()]
    if not checks:
        _fail("invalid_argument", "--checks must contain at least one check id", exit_code=2)
    return checks


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return value


def _request(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
    base = _base_url()
    token = _api_key()
    query = urllib.parse.urlencode(_clean_params(params or {}))
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}"
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "dba-skill-client",
        "X-API-Key": token,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        _fail(
            "http_error",
            f"{method} {path} returned HTTP {exc.code}",
            status_code=exc.code,
            response=_redact(raw),
        )
    except urllib.error.URLError as exc:
        _fail("network_error", f"{method} {path} failed: {exc.reason}")
    except TimeoutError:
        _fail("network_error", f"{method} {path} timed out")
    except json.JSONDecodeError as exc:
        _fail("invalid_json", f"{method} {path} returned invalid JSON: {exc}")


def _common_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id")
    parser.add_argument("--instance-type")
    parser.add_argument("--instance-id", type=int)
    parser.add_argument("--department")
    parser.add_argument("--service-domain")
    parser.add_argument("--business")
    parser.add_argument("--contact-person")
    parser.add_argument("--technical-contact")
    parser.add_argument("--contact")
    parser.add_argument("--contact-role", choices=["any", "application", "technical"])
    parser.add_argument("--q")


def _common_filter_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "tenant_id": args.tenant_id,
        "instance_type": args.instance_type,
        "instance_id": args.instance_id,
        "department": args.department,
        "service_domain": args.service_domain,
        "business": args.business,
        "contact_person": args.contact_person,
        "technical_contact": args.technical_contact,
        "contact": args.contact,
        "contact_role": args.contact_role,
        "q": args.q,
    }


def cmd_resolve(args: argparse.Namespace) -> Any:
    params = _common_filter_params(args)
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
    return _request("GET", "/dba/resolve", params=params)


def cmd_context(args: argparse.Namespace) -> Any:
    return _request(
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


def cmd_alert_evidence(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        f"/dba/alerts/{args.alert_id}/evidence",
        params={"before_hours": args.before_hours, "after_hours": args.after_hours},
    )


def cmd_alerts_list(args: argparse.Namespace) -> Any:
    return _request(
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


def cmd_classification(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/instances/classification",
        params={
            "type": args.type,
            "topology": args.topology,
            "tenant_id": args.tenant_id,
        },
    )


def cmd_inventory_summary(args: argparse.Namespace) -> Any:
    params = _common_filter_params(args)
    params.update(
        {
            "include_system_dbs": args.include_system_dbs,
            "stale_after_hours": args.stale_after_hours,
        }
    )
    return _request("GET", "/dba/inventory/summary", params=params)


def cmd_databases_search(args: argparse.Namespace) -> Any:
    params = _common_filter_params(args)
    params.update(
        {
            "status": args.status,
            "include_inactive": args.include_inactive,
            "include_system_dbs": args.include_system_dbs,
            "is_in_use": args.is_in_use,
            "limit": args.limit,
            "offset": args.offset,
        }
    )
    return _request("GET", "/dba/databases/search", params=params)


def cmd_databases_unused(args: argparse.Namespace) -> Any:
    params = _common_filter_params(args)
    params.update({"include_inactive": args.include_inactive, "limit": args.limit})
    return _request("GET", "/dba/databases/unused", params=params)


def cmd_ownership_scope(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/dba/ownership/scope",
        params={
            "contact": args.contact,
            "contact_role": args.contact_role,
            "department": args.department,
            "service_domain": args.service_domain,
            "business": args.business,
            "tenant_id": args.tenant_id,
            "include_inactive": args.include_inactive,
            "include_system_dbs": args.include_system_dbs,
            "stale_after_hours": args.stale_after_hours,
        },
    )


def cmd_directory_options(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/dba/directory/options",
        params={
            "type": args.type,
            "search": args.search,
            "include_inactive": args.include_inactive,
            "limit": args.limit,
        },
    )


def cmd_freshness(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        f"/dba/instances/{args.instance_id}/freshness",
        params={"stale_after_hours": args.stale_after_hours},
    )


def cmd_timeline(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        f"/dba/instances/{args.instance_id}/timeline",
        params={"hours": args.hours, "limit": args.limit},
    )


def cmd_diagnostics_catalog(args: argparse.Namespace) -> Any:
    return _request("GET", f"/dba/instances/{args.instance_id}/diagnostics/catalog")


def cmd_diagnostics_run(args: argparse.Namespace) -> Any:
    if args.sql:
        _fail(
            "free_form_sql_not_supported",
            "diagnostics-run only accepts catalog check ids; free-form SQL is not supported",
            exit_code=2,
        )
    body: dict[str, Any] = {"checks": _checks(args.checks)}
    if args.timeout_seconds is not None:
        body["timeout_seconds"] = args.timeout_seconds
    if args.database_name:
        body["database_name"] = args.database_name
    return _request("POST", f"/dba/instances/{args.instance_id}/diagnostics/run", body=body)


def cmd_probe_catalog(args: argparse.Namespace) -> Any:
    return _request("GET", f"/instances/{args.instance_id}/diagnostics/catalog")


def cmd_ai_endpoints(args: argparse.Namespace) -> Any:
    _ = args
    return _request("GET", "/ai-endpoints")


def _normalize_read_path(raw: str) -> str:
    """Normalize a catalog path onto the base URL, which already ends in /api/v<n>.
    The ai-endpoints catalog returns full paths like /api/v2/topology, so strip a
    leading /api/vN to avoid doubling the version prefix; accept bare paths too."""
    path = raw.strip()
    if not path.startswith("/"):
        path = "/" + path
    for prefix in ("/api/v2", "/api/v1"):
        if path == prefix or path.startswith(prefix + "/"):
            path = path[len(prefix):] or "/"
            break
    return path


def _parse_kv_params(raw_params: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw_params or []:
        key, sep, value = item.partition("=")
        if sep != "=" or not key.strip():
            _fail("invalid_argument", f"--param must be key=value, got: {item}", exit_code=2)
        params[key.strip()] = value
    return params


def cmd_get(args: argparse.Namespace) -> Any:
    path = _normalize_read_path(args.path)
    return _request("GET", path, params=_parse_kv_params(args.param))


def cmd_probe_run(args: argparse.Namespace) -> Any:
    if args.sql:
        _fail(
            "free_form_sql_not_supported",
            "probe-run only accepts a whitelisted probe name + bound params; free-form SQL is not supported",
            exit_code=2,
        )
    params: dict[str, Any] = {}
    if args.sql_id:
        params["sql_id"] = args.sql_id
    if args.session_id is not None:
        params["session_id"] = args.session_id
    if args.object_name:
        params["object_name"] = args.object_name
    body: dict[str, Any] = {"probe": args.probe}
    if params:
        body["params"] = params
    return _request("POST", f"/instances/{args.instance_id}/diagnostics/probe", body=body)


def cmd_prometheus_query(args: argparse.Namespace) -> Any:
    body: dict[str, Any] = {"query": args.query}
    if args.url:
        body["url"] = args.url
    return _request("POST", f"/instances/{args.instance_id}/prometheus/query", body=body)


def cmd_kb_search(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/knowledge/entries",
        params={
            "q": args.q,
            "keyword": args.keyword,
            "db_type": args.db_type,
            "rule_id": args.rule_id,
            "sort": args.sort,
            "limit": args.limit,
            "offset": args.offset,
        },
    )


def cmd_kb_incidents(args: argparse.Namespace) -> Any:
    key = urllib.parse.quote(args.root_cause_key, safe="")
    return _request(
        "GET",
        f"/knowledge/entries/{key}/incidents",
        params={"db_type": args.db_type, "rule_id": args.rule_id, "limit": args.limit},
    )


def cmd_kb_doc_search(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/knowledge/documents/search",
        params={"q": args.q, "limit": args.limit},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call Database AI Center DBA APIs safely.")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve")
    _common_filters(resolve)
    resolve.add_argument("--host")
    resolve.add_argument("--ip")
    resolve.add_argument("--instance-name")
    resolve.add_argument("--database-name")
    resolve.add_argument("--alert-id", type=int)
    resolve.add_argument("--limit", type=int)
    resolve.set_defaults(func=cmd_resolve)

    context = sub.add_parser("context")
    context.add_argument("--alert-id", type=int)
    context.add_argument("--instance-id", type=int)
    context.add_argument("--database-id", type=int)
    context.add_argument("--refresh-ai-context", action="store_true")
    context.add_argument("--stale-after-hours", type=int)
    context.set_defaults(func=cmd_context)

    alert_evidence = sub.add_parser("alert-evidence")
    alert_evidence.add_argument("--alert-id", type=int, required=True)
    alert_evidence.add_argument("--before-hours", type=int)
    alert_evidence.add_argument("--after-hours", type=int)
    alert_evidence.set_defaults(func=cmd_alert_evidence)

    alerts = sub.add_parser("alerts-list")
    alerts.add_argument("--status", default="active")
    alerts.add_argument("--all-statuses", action="store_true")
    alerts.add_argument("--severity")
    alerts.add_argument("--tenant-id")
    alerts.add_argument("--instance-id", type=int)
    alerts.add_argument("--page", type=_positive_int, default=1)
    alerts.add_argument("--page-size", type=_positive_int, default=20)
    alerts.add_argument("--start-time")
    alerts.add_argument("--end-time")
    alerts.set_defaults(func=cmd_alerts_list)

    classification = sub.add_parser("classification")
    classification.add_argument("--type", choices=["mysql", "postgres", "oracle", "tidb", "clickhouse"])
    classification.add_argument(
        "--topology",
        help="Filter by topology kind: rac, dataguard, mysql_replication, mysql_group_replication, postgres_replication, tidb_cluster, clickhouse_cluster, replication, standalone",
    )
    classification.add_argument("--tenant-id")
    classification.set_defaults(func=cmd_classification)

    inventory = sub.add_parser("inventory-summary")
    _common_filters(inventory)
    inventory.add_argument("--include-system-dbs", action="store_true")
    inventory.add_argument("--stale-after-hours", type=int)
    inventory.set_defaults(func=cmd_inventory_summary)

    search = sub.add_parser("databases-search")
    _common_filters(search)
    search.add_argument("--status")
    search.add_argument("--include-inactive", action="store_true")
    search.add_argument("--include-system-dbs", action="store_true")
    search.add_argument("--is-in-use", choices=["true", "false"])
    search.add_argument("--limit", type=int)
    search.add_argument("--offset", type=int)
    search.set_defaults(func=cmd_databases_search)

    unused = sub.add_parser("databases-unused")
    _common_filters(unused)
    unused.add_argument("--include-inactive", action="store_true")
    unused.add_argument("--limit", type=int)
    unused.set_defaults(func=cmd_databases_unused)

    scope = sub.add_parser("ownership-scope")
    scope.add_argument("--contact")
    scope.add_argument("--contact-role", choices=["any", "application", "technical"])
    scope.add_argument("--department")
    scope.add_argument("--service-domain")
    scope.add_argument("--business")
    scope.add_argument("--tenant-id")
    scope.add_argument("--include-inactive", action="store_true")
    scope.add_argument("--include-system-dbs", action="store_true")
    scope.add_argument("--stale-after-hours", type=int)
    scope.set_defaults(func=cmd_ownership_scope)

    directory = sub.add_parser("directory-options")
    directory.add_argument("--type", choices=["contact", "department", "application"], required=True)
    directory.add_argument("--search")
    directory.add_argument("--include-inactive", action="store_true")
    directory.add_argument("--limit", type=int)
    directory.set_defaults(func=cmd_directory_options)

    freshness = sub.add_parser("freshness")
    freshness.add_argument("--instance-id", type=int, required=True)
    freshness.add_argument("--stale-after-hours", type=int)
    freshness.set_defaults(func=cmd_freshness)

    timeline = sub.add_parser("timeline")
    timeline.add_argument("--instance-id", type=int, required=True)
    timeline.add_argument("--hours", type=int)
    timeline.add_argument("--limit", type=int)
    timeline.set_defaults(func=cmd_timeline)

    catalog = sub.add_parser("diagnostics-catalog")
    catalog.add_argument("--instance-id", type=int, required=True)
    catalog.set_defaults(func=cmd_diagnostics_catalog)

    run = sub.add_parser("diagnostics-run")
    run.add_argument("--instance-id", type=int, required=True)
    run.add_argument("--checks", required=True)
    run.add_argument("--timeout-seconds", type=int)
    run.add_argument("--database-name")
    run.add_argument("--sql", help=argparse.SUPPRESS)
    run.set_defaults(func=cmd_diagnostics_run)

    ai_endpoints = sub.add_parser(
        "ai-endpoints",
        help="List the self-describing catalog of model-reachable (ai-client) read endpoints.",
    )
    ai_endpoints.set_defaults(func=cmd_ai_endpoints)

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
    get_cmd.set_defaults(func=cmd_get)

    probe_catalog = sub.add_parser("probe-catalog")
    probe_catalog.add_argument("--instance-id", type=int, required=True)
    probe_catalog.set_defaults(func=cmd_probe_catalog)

    probe_run = sub.add_parser("probe-run")
    probe_run.add_argument("--instance-id", type=int, required=True)
    probe_run.add_argument("--probe", required=True)
    probe_run.add_argument("--sql-id")
    probe_run.add_argument("--session-id", type=int)
    probe_run.add_argument("--object-name")
    probe_run.add_argument("--sql", help=argparse.SUPPRESS)
    probe_run.set_defaults(func=cmd_probe_run)

    prometheus_query = sub.add_parser(
        "prometheus-query",
        help="Read-only instant PromQL against a TiDB instance's Prometheus (hotspots, "
        "golden signals, per-store flow, metric-name verification). Read-only, SSRF-guarded.",
    )
    prometheus_query.add_argument("--instance-id", type=int, required=True)
    prometheus_query.add_argument("--query", required=True, help="a single instant PromQL expression")
    prometheus_query.add_argument("--url", help="override Prometheus URL (defaults to saved extra.prometheus_url)")
    prometheus_query.set_defaults(func=cmd_prometheus_query)

    _KB_DB_TYPES = ["oracle", "mysql", "postgres", "tidb", "clickhouse"]

    kb_search = sub.add_parser(
        "kb-search", help="Knowledge base: DBA-confirmed symptom->root-cause->remediation history"
    )
    kb_search.add_argument("--q", help="semantic query (embeds the text; widened recall)")
    kb_search.add_argument("--keyword", help="literal keyword match, e.g. ORA-00060")
    kb_search.add_argument("--db-type", choices=_KB_DB_TYPES)
    kb_search.add_argument("--rule-id")
    kb_search.add_argument("--sort", choices=["frequency", "recency"])
    kb_search.add_argument("--limit", type=int)
    kb_search.add_argument("--offset", type=int)
    kb_search.set_defaults(func=cmd_kb_search)

    kb_incidents = sub.add_parser(
        "kb-incidents", help="Knowledge base: drill down to the raw incidents behind one root cause"
    )
    kb_incidents.add_argument("--root-cause-key", required=True)
    kb_incidents.add_argument("--db-type", choices=_KB_DB_TYPES)
    kb_incidents.add_argument("--rule-id")
    kb_incidents.add_argument("--limit", type=int)
    kb_incidents.set_defaults(func=cmd_kb_incidents)

    kb_doc_search = sub.add_parser(
        "kb-doc-search", help="Knowledge base: semantic search over curated ops-runbook documents"
    )
    kb_doc_search.add_argument("--q", required=True)
    kb_doc_search.add_argument("--limit", type=int)
    kb_doc_search.set_defaults(func=cmd_kb_doc_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = args.func(args)
    _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
