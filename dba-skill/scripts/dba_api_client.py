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


# Where the credentials came from, so an auth failure can say so instead of looking like a
# revoked key. Filled by _load_env_files.
_ENV_PROVENANCE: dict[str, Any] = {"source": None, "searched": [], "keys": []}


def _credential_provenance() -> dict[str, Any]:
    source = _ENV_PROVENANCE.get("source")
    return {
        "credential_source": source or "inherited process environment (no .env file found)",
        "env_files_searched": _ENV_PROVENANCE.get("searched") or [],
        "cwd": os.getcwd(),
    }


def _load_env_files() -> None:
    """Load the nearest .env, and remember whether one was found.

    Provenance is the point. The documented production setup supplies the key through the
    process environment (the agent runtime's own config), so *failing* when no .env is found
    would break the normal case. But the silent version is worse than either: run from the
    wrong directory and the stale inherited key is used without a word, so a 401 reads as
    "the key was revoked" when it actually means "you are in the wrong directory". The fix is
    not to refuse — it is to make every auth failure say where its credential came from.
    """
    global _ENV_FILES_LOADED
    if _ENV_FILES_LOADED:
        return
    _ENV_FILES_LOADED = True

    try:
        search_roots = (Path.cwd().resolve(), *Path.cwd().resolve().parents)
    except OSError:
        _ENV_PROVENANCE["searched"] = ["<cwd unavailable>"]
        return
    _ENV_PROVENANCE["searched"] = [str(d) for d in search_roots]

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
                _ENV_PROVENANCE.setdefault("keys", []).append(key)
            _ENV_PROVENANCE["source"] = str(path)
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


# Set from --all. A partial page and a complete one look identical, so opting into the whole
# answer has to be one flag, not a paging loop the caller has to write correctly every time.
_FETCH_ALL = False

# Last HTTP status seen, so the /dba prefix retry fires only for a genuine 404. A 401 is not
# a path problem: retrying it just emits a second identical failure and buries the first.
_LAST_HTTP_STATUS: int | None = None


def _request(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
    """Every call goes through here: pages when asked, and always says when a page is partial."""
    if method == "GET" and _FETCH_ALL:
        return _fetch_all(method, path, dict(params or {}))
    payload = _request_once(method, path, params=params, body=body)
    if method == "GET":
        _warn_if_truncated(payload, path)
    return payload


def _envelope(payload: Any) -> tuple[list[Any] | None, dict[str, Any]]:
    """Split a response into (items, meta) across the three shapes the platform returns.

    There is no single envelope: `ai-endpoints` and `metrics/{id}/latest` return a bare list;
    `instances` and `instances/database-inventory` return
    {items, total, limit, offset, truncated}; `alerts` returns
    {items, total, page, page_size, has_next} — a different pagination vocabulary again.
    Every consumer otherwise has to probe defensively, and guessing wrong turns into
    'str' object has no attribute 'get' at the worst moment.

    Returns (None, {}) for a single object, which is not a collection and must not be
    silently treated as one.
    """
    if isinstance(payload, list):
        return payload, {"shape": "list", "total": len(payload)}
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        meta = {key: value for key, value in payload.items() if key != "items"}
        meta["shape"] = "envelope"
        return payload["items"], meta
    return None, {}


def _warn_if_truncated(payload: Any, path: str) -> None:
    """Say so on stderr when a page is not the whole answer.

    A truncated page and a complete one are the same shape, so "no rows matched" and "your
    row is on page 2" are indistinguishable without reading `total`. Production 2026-09-01: a
    caller asked which instances had no databases, got 2000 of 2072 rows, and the two
    instances it was looking for were in the missing 72.
    """
    if not isinstance(payload, dict):
        return
    items, meta = _envelope(payload)
    if items is None:
        return
    total = meta.get("total")
    if meta.get("truncated") is True or (isinstance(total, int) and total > len(items)):
        sys.stderr.write(
            json.dumps(
                {
                    "warning": "partial_result",
                    "message": (
                        f"{path} returned {len(items)} of {total} rows. This page is NOT the "
                        f"whole answer — re-run with --all, or page with offset/page."
                    ),
                    "returned": len(items),
                    "total": total,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _fetch_all(method: str, path: str, params: dict[str, Any], *, page_limit: int = 50) -> Any:
    """Follow pagination to the end, for both vocabularies the platform uses.

    ``page_limit`` is a guard, not a preference: an endpoint that never advances would
    otherwise loop forever, and a silent infinite loop is worse than a partial answer that
    says it is partial.
    """
    first = _request_once(method, path, params=dict(params))
    items, meta = _envelope(first)
    if items is None or meta.get("shape") != "envelope":
        return first

    collected = list(items)
    total = meta.get("total")
    if not isinstance(total, int):
        return first

    if "offset" in meta or "limit" in meta:
        size = int(meta.get("limit") or len(items) or 1)
        pages = 0
        while len(collected) < total and pages < page_limit and size > 0:
            pages += 1
            nxt = _request_once(method, path, params={**params, "limit": size, "offset": len(collected)})
            more, _ = _envelope(nxt)
            if not more:
                break
            collected.extend(more)
    elif "page" in meta or "page_size" in meta:
        size = int(meta.get("page_size") or len(items) or 1)
        page = int(meta.get("page") or 1)
        pages = 0
        while len(collected) < total and pages < page_limit and size > 0:
            pages += 1
            page += 1
            nxt = _request_once(method, path, params={**params, "page": page, "page_size": size})
            more, _ = _envelope(nxt)
            if not more:
                break
            collected.extend(more)

    out = {key: value for key, value in first.items() if key != "items"}
    out["items"] = collected
    out["truncated"] = len(collected) < total
    out["fetched_all"] = len(collected) >= total
    return out


def _project(payload: Any, fields: list[str] | None) -> Any:
    """Keep only the requested fields, so a 400 KB dump can be four columns."""
    if not fields:
        return payload
    items, meta = _envelope(payload)
    if items is None:
        return {key: payload.get(key) for key in fields} if isinstance(payload, dict) else payload
    picked = [
        {key: row.get(key) for key in fields} if isinstance(row, dict) else row
        for row in items
    ]
    if meta.get("shape") == "list":
        return picked
    out = {key: value for key, value in payload.items() if key != "items"}
    out["items"] = picked
    return out


def _print_table(payload: Any) -> bool:
    """Render a collection as columns. Returns False when the payload is not tabular."""
    items, _ = _envelope(payload)
    if not items or not all(isinstance(row, dict) for row in items):
        return False
    columns: list[str] = []
    for row in items:
        for key in row:
            if key not in columns:
                columns.append(key)
    widths = {c: max(len(c), *(len(_cell(r.get(c))) for r in items)) for c in columns}
    sys.stdout.write("  ".join(c.ljust(widths[c]) for c in columns).rstrip() + "\n")
    sys.stdout.write("  ".join("-" * widths[c] for c in columns) + "\n")
    for row in items:
        sys.stdout.write("  ".join(_cell(row.get(c)).ljust(widths[c]) for c in columns).rstrip() + "\n")
    return True


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:60]
    return str(value)


def _request_once(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
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
        global _LAST_HTTP_STATUS
        _LAST_HTTP_STATUS = exc.code
        extra: dict[str, Any] = {"status_code": exc.code, "response": _redact(raw)}
        if exc.code in (401, 403):
            # "Wrong key" and "wrong directory" produce the same 401. Say which one this is.
            extra["credentials"] = _credential_provenance()
        _fail(
            "http_error",
            f"{method} {path} returned HTTP {exc.code}",
            **extra,
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


def cmd_alerts(args: argparse.Namespace) -> Any:
    """Flat current-alert list (platform v3.32+).

    Prefer this over ``alerts-list``: the instance name/host/type are inlined and the
    triggering actual/threshold are lifted out of the evidence blob, so answering "what is
    alerting right now" is one call with no per-alert follow-up and no wading through the
    platform's internal ``_dac_*`` bookkeeping fields.
    """
    return _request(
        "GET",
        "/dba/alerts",
        params={
            "status": args.status,
            "severity": args.severity,
            "instance_id": args.instance_id,
            "limit": args.limit,
        },
    )


def cmd_backups_coverage(args: argparse.Namespace) -> Any:
    """Fleet backup coverage across BOTH tracks (local RMAN/expdp + offsite NAS).

    Defaults to ``at_risk`` because that is what the question almost always means; pass
    ``--verdict all`` for the whole estate. ``not_applicable`` covers cloud RDS (the provider
    backs those up) and cluster components (backup is cluster-level) — counting them as
    at-risk buries the real findings, so they are a separate bucket, not a failure.
    """
    verdict = None if args.verdict == "all" else args.verdict
    return _request(
        "GET",
        "/dba/backups/coverage",
        params={"verdict": verdict, "instance_type": args.instance_type, "limit": args.limit},
    )


def cmd_capacity_forecast(args: argparse.Namespace) -> Any:
    """Projected resource exhaustion — including trends too far out to alert.

    ``would_alert`` is reported per row rather than used as a filter: a tablespace 200 days
    from full never appears in the alert list, and that lead time is the whole point. Pass
    ``--include-gaps`` to also see the instances that could NOT be projected, with reasons —
    an instance missing from the list is not the same as an instance with no risk.
    """
    return _request(
        "GET",
        "/dba/capacity/forecast",
        params={
            "metric_name": args.metric_name,
            "max_days": args.max_days,
            "include_gaps": "true" if args.include_gaps else None,
            "limit": args.limit,
        },
    )


def cmd_silence_report(args: argparse.Namespace) -> Any:
    """Why one instance might not be alerting — all five mechanisms, active or not.

    Checking two of the five and concluding "nothing is suppressed" is the failure this
    replaces; inactive mechanisms are listed precisely so the caller can tell it looked
    everywhere.
    """
    return _request("GET", "/dba/instances/{0}/silence-report".format(args.instance_id))


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


def _try_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET that returns an error dict instead of exiting, for the composite commands.

    A composite answer must not vanish because one of its parts is unavailable — that is the
    difference between "this instance has no backup row" and "the whole question failed".
    """
    try:
        return _request_once("GET", path, params=params)
    except SystemExit:
        return {"unavailable": True, "path": path, "status_code": _LAST_HTTP_STATUS}


def cmd_instance(args: argparse.Namespace) -> Any:
    """Everything about one instance, from an id or an IP, in one call.

    "Here is an IP, tell me about this box" is the most frequent question and it used to take
    several calls across two path prefixes. Each part is fetched independently and a missing
    part is reported as `unavailable` rather than collapsing the whole answer.
    """
    instance_id = args.instance_id
    resolved: Any = None
    if instance_id is None:
        if not (args.ip or args.host):
            _fail("invalid_argument", "instance requires --instance-id, --ip or --host", exit_code=2)
        resolved = _try_get("/dba/resolve", _clean_params({"ip": args.ip, "host": args.host}))
        for key in ("instances", "matches", "items"):
            rows = resolved.get(key) if isinstance(resolved, dict) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                instance_id = rows[0].get("instance_id") or rows[0].get("id")
                break
        if instance_id is None:
            _fail("not_found", f"no instance matched ip={args.ip} host={args.host}",
                  exit_code=1, resolve_response=resolved)

    detail = _try_get(f"/instances/{instance_id}")
    databases = _try_get("/databases", {"instance_id": instance_id, "limit": 1})
    alerts = _try_get("/alerts", {"instance_id": instance_id, "status": "active", "page_size": 50})
    alert_items, _ = _envelope(alerts)
    db_items, db_meta = _envelope(databases)
    return {
        "instance_id": instance_id,
        "instance": detail,
        "freshness": _try_get(f"/dba/instances/{instance_id}/freshness"),
        "backups": _try_get(f"/instances/{instance_id}/backups"),
        "database_count": db_meta.get("total") if db_meta else (len(db_items) if db_items else None),
        "database_inventory_coverage": (detail or {}).get("database_inventory_coverage")
        if isinstance(detail, dict) else None,
        "active_alerts": alert_items or [],
        "resolved_from": {"ip": args.ip, "host": args.host} if resolved is not None else None,
    }


# What a newly onboarded instance is usually missing. Each is a separate subsystem, so
# "metrics look fine" says nothing about any of them.
_ONBOARDING_CHECKS = ("database_inventory", "backup_method", "ownership", "elk_logs")


def cmd_onboarding_check(args: argparse.Namespace) -> Any:
    """Is this newly onboarded instance actually wired up?

    Metrics start flowing immediately, which is exactly what makes the rest easy to miss:
    databases undiscovered, backup method undeclared, no owner, not shipping logs. Four
    subsystems, four separate answers — this asks all four and says which are missing.
    """
    iid = args.instance_id
    detail = _try_get(f"/instances/{iid}")
    databases = _try_get("/databases", {"instance_id": iid, "limit": 1})
    backups = _try_get(f"/instances/{iid}/backups")
    elk = _try_get("/elk/coverage")

    db_items, db_meta = _envelope(databases)
    db_count = db_meta.get("total") if db_meta else (len(db_items) if db_items else 0)
    coverage = (detail or {}).get("database_inventory_coverage") if isinstance(detail, dict) else None
    method = (backups or {}).get("backup_method") if isinstance(backups, dict) else None
    contact = (detail or {}).get("contact_person") if isinstance(detail, dict) else None
    host = (detail or {}).get("host") if isinstance(detail, dict) else None

    # /elk/coverage is a fourth shape again: the rows live under `instances`, each carrying
    # its own `covered` flag. Matching on `items` + host returned an empty set and reported
    # every instance as "not shipping logs" — a wrong-field lookup and a genuine gap produce
    # the same empty answer, which is the whole reason this command reports `unavailable`
    # separately from `ok: false`.
    elk_rows = elk.get("instances") if isinstance(elk, dict) else None
    elk_covered: set[int] = {
        int(row["id"]) for row in (elk_rows or [])
        if isinstance(row, dict) and row.get("covered") and row.get("id") is not None
    }
    elk_readable = isinstance(elk_rows, list)

    findings = {
        # An empty list on a cluster member is correct: the owner holds the rows.
        "database_inventory": {
            "ok": bool(db_count) or coverage in ("cluster_covered", "cluster_component"),
            "databases": db_count, "coverage": coverage,
        },
        "backup_method": {"ok": bool(method), "backup_method": method,
                          "why": "undeclared makes 'no backup' and 'expdp not declared' indistinguishable"},
        "ownership": {"ok": bool(contact), "contact_person": contact},
        # Unreadable coverage is not the same as uncovered: say which one it is.
        "elk_logs": (
            {"ok": int(iid) in elk_covered, "host": host}
            if elk_readable
            else {"ok": None, "host": host, "unavailable": "could not read /elk/coverage"}
        ),
    }
    missing = [name for name, f in findings.items() if f["ok"] is False]
    unknown = [name for name, f in findings.items() if f["ok"] is None]
    return {
        "instance_id": iid,
        "checks": findings,
        "missing": missing,
        # A check the platform could not answer is reported apart from one it answered "no":
        # collapsing them would let an outage read as a clean bill of health.
        "unknown": unknown,
        "ok": not missing and not unknown,
    }


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


def _split_fields(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    fields = [part.strip() for part in raw.split(",") if part.strip()]
    return fields or None


def _parse_kv_params(raw_params: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw_params or []:
        key, sep, value = item.partition("=")
        if sep != "=" or not key.strip():
            _fail("invalid_argument", f"--param must be key=value, got: {item}", exit_code=2)
        params[key.strip()] = value
    return params


def cmd_get(args: argparse.Namespace) -> Any:
    """Read any catalogue path, tolerating the /dba prefix being present or absent.

    The catalogue mixes both: freshness lives under /dba/instances/{id}/freshness while the
    instance detail is the bare /instances/{id}. Getting it wrong returns a 404 that reads
    like "this instance does not exist" rather than "you used the wrong prefix", so the
    alternative is tried once before reporting failure.
    """
    path = _normalize_read_path(args.path)
    params = _parse_kv_params(args.param)
    try:
        return _request("GET", path, params=params)
    except SystemExit:
        if _LAST_HTTP_STATUS != 404:
            raise
        alternative = path[len("/dba"):] if path.startswith("/dba/") else "/dba" + path
        if alternative == path:
            raise
        try:
            payload = _request("GET", alternative, params=params)
        except SystemExit:
            raise SystemExit(1) from None
        sys.stderr.write(
            json.dumps(
                {"warning": "path_prefix_corrected", "requested": path, "used": alternative},
                ensure_ascii=False,
            )
            + "\n"
        )
        return payload


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


def cmd_elk_status(args: argparse.Namespace) -> Any:
    return _request("GET", "/elk/status")


def cmd_elk_coverage(args: argparse.Namespace) -> Any:
    return _request("GET", "/elk/coverage")


def cmd_elk_search(args: argparse.Namespace) -> Any:
    return _request(
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


def cmd_cloud_rightsizing(args: argparse.Namespace) -> Any:
    return _request(
        "GET",
        "/cloud-rds/rightsizing",
        params={
            "window_days": args.window_days,
            "cpu_max": args.cpu_max,
            "mem_max": args.mem_max,
            "vendor": args.vendor,
        },
    )


def cmd_cloud_cost_history(args: argparse.Namespace) -> Any:
    return _request("GET", "/cloud-rds/cost-history")


def cmd_backups(args: argparse.Namespace) -> Any:
    # Surfaces backup_method + determination (verified / declared_no_evidence / not_tracked /
    # unknown) so the answer to "does this instance actually have a backup?" is explicit —
    # expdp dumps are invisible to RMAN, so the raw status alone reads as a false "no backup".
    return _request(
        "GET",
        f"/instances/{args.instance_id}/backups",
        params={"refresh": args.refresh or None},
    )


def _add_global_output_flags(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    """Register the output flags.

    ``suppress_defaults`` matters on the subparser copies: argparse applies a subparser's
    defaults *after* the top-level namespace, so a plain default would overwrite a flag the
    caller passed before the subcommand — `--all get ...` would be accepted and silently do
    nothing. SUPPRESS makes the subparser contribute the value only when it was actually
    given, so both orders work.
    """
    default: Any = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--all", action="store_true", default=(argparse.SUPPRESS if suppress_defaults else False),
        help="Follow pagination to the end. Without it a large collection returns one page, "
             "and a partial page is shaped exactly like a complete one.",
    )
    parser.add_argument(
        "--fields", default=default,
        help="Comma-separated fields to keep from each row (e.g. id,host,type,status). "
             "A full inventory dump is hundreds of KB; four columns is usually the answer.",
    )
    parser.add_argument(
        "--format", choices=["json", "table"],
        default=(argparse.SUPPRESS if suppress_defaults else "json"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call Database AI Center DBA APIs safely.")
    _add_global_output_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    instance = sub.add_parser(
        "instance",
        help="Everything about one instance from an id or an IP: detail, freshness, backups, "
             "database count/coverage, and active alerts — in one call.",
    )
    instance.add_argument("--instance-id", type=int)
    instance.add_argument("--ip")
    instance.add_argument("--host")
    instance.set_defaults(func=cmd_instance)

    onboarding = sub.add_parser(
        "onboarding-check",
        help="Is a newly onboarded instance actually wired up? Checks database inventory, "
             "declared backup method, ownership and ELK log coverage — metrics flowing says "
             "nothing about any of them.",
    )
    onboarding.add_argument("--instance-id", type=int, required=True)
    onboarding.set_defaults(func=cmd_onboarding_check)

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

    alerts_v2 = sub.add_parser(
        "alerts",
        help="Current alerts, flat, instance inlined (v3.32+; prefer over alerts-list)",
    )
    alerts_v2.add_argument("--status", default="active", choices=["active", "resolved", "all"])
    alerts_v2.add_argument("--severity")
    alerts_v2.add_argument("--instance-id", type=int)
    alerts_v2.add_argument("--limit", type=_positive_int, default=200)
    alerts_v2.set_defaults(func=cmd_alerts)

    bcov = sub.add_parser(
        "backups-coverage",
        help="Fleet backup coverage, both tracks (v3.32+); defaults to at_risk only",
    )
    bcov.add_argument("--verdict", default="at_risk",
                      choices=["at_risk", "ok", "warning", "indeterminate",
                               "remote_untracked", "not_applicable", "suppressed", "all"])
    bcov.add_argument("--instance-type")
    bcov.add_argument("--limit", type=_positive_int, default=500)
    bcov.set_defaults(func=cmd_backups_coverage)

    capf = sub.add_parser(
        "capacity-forecast",
        help="Projected exhaustion incl. trends below the alert threshold (v3.32+)",
    )
    capf.add_argument("--metric-name")
    capf.add_argument("--max-days", type=float)
    capf.add_argument("--include-gaps", action="store_true")
    capf.add_argument("--limit", type=_positive_int, default=500)
    capf.set_defaults(func=cmd_capacity_forecast)

    silr = sub.add_parser(
        "silence-report",
        help="Why an instance might not be alerting: all 5 mechanisms (v3.32+)",
    )
    silr.add_argument("--instance-id", type=int, required=True)
    silr.set_defaults(func=cmd_silence_report)

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

    elk_status = sub.add_parser(
        "elk-status", help="ELK connectivity + which DB-log indices exist (v2.24+)"
    )
    elk_status.set_defaults(func=cmd_elk_status)

    elk_coverage = sub.add_parser(
        "elk-coverage", help="Managed instances vs ELK log coverage (who is / isn't shipping logs)"
    )
    elk_coverage.set_defaults(func=cmd_elk_coverage)

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
    elk_search.set_defaults(func=cmd_elk_search)

    cloud_rightsizing = sub.add_parser(
        "cloud-rightsizing",
        help="Cloud RDS right-sizing readout: per-instance peaks, downsize candidates, cost + saving (v2.74+)",
    )
    cloud_rightsizing.add_argument("--window-days", type=int, help="Trailing peak window (1-90, default 90)")
    cloud_rightsizing.add_argument("--cpu-max", type=float, help="Candidate CPU ceiling %% (default 40)")
    cloud_rightsizing.add_argument("--mem-max", type=float, help="Memory-pressure impediment %% (default 70)")
    cloud_rightsizing.add_argument("--vendor", choices=["aliyun", "huawei"], help="Restrict to one provider")
    cloud_rightsizing.set_defaults(func=cmd_cloud_rightsizing)

    cloud_cost_history = sub.add_parser(
        "cloud-cost-history", help="Cloud RDS billing history (gross / paid / coupon by month + year)"
    )
    cloud_cost_history.set_defaults(func=cmd_cloud_cost_history)

    backups = sub.add_parser(
        "backups",
        help="One instance's backup status + evidence-based determination (has-backup verdict; v2.98+)",
    )
    backups.add_argument("--instance-id", type=int, required=True)
    backups.add_argument(
        "--refresh", action="store_true",
        help="Force a live Oracle RMAN refresh (slow); default serves the stored daily-swept status",
    )
    backups.set_defaults(func=cmd_backups)

    # Accept the flags after the subcommand too — `inventory-summary --all` is what anyone
    # types first, and argparse would otherwise only honour them before the subcommand.
    for action in sub.choices.values():
        _add_global_output_flags(action, suppress_defaults=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    global _FETCH_ALL
    _FETCH_ALL = bool(getattr(args, "all", False))
    payload = args.func(args)
    payload = _project(payload, _split_fields(getattr(args, "fields", None)))
    if getattr(args, "format", "json") == "table" and _print_table(payload):
        return 0
    _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
