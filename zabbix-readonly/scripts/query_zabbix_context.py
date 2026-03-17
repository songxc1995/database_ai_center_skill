#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
from dataclasses import dataclass
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_token: str
    host_ip: str | None
    host_name: str | None
    zabbix_hostid: str | None
    time_range_minutes: int
    timeout_seconds: float
    verify_tls: bool


def _env_or_default(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    if value is None:
        return default
    text = value.strip()
    return text if text else default


def _bool_from_text(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _build_ssl_context(verify_tls: bool) -> ssl.SSLContext | None:
    if verify_tls:
        return None
    return ssl._create_unverified_context()


def _rpc_call(
    config: RuntimeConfig,
    method: str,
    params: dict[str, object],
    request_id: int,
) -> object:
    payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "auth": config.api_token,
        "id": request_id,
    }
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = Request(
        url=f"{config.base_url.rstrip('/')}/api_jsonrpc.php",
        data=body,
        headers={"Content-Type": "application/json-rpc"},
        method="POST",
    )
    context = _build_ssl_context(verify_tls=config.verify_tls)
    try:
        with urlopen(request, timeout=config.timeout_seconds, context=context) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Invalid Zabbix response payload.")
    error_obj = parsed.get("error")
    if isinstance(error_obj, dict):
        error_message = str(error_obj.get("message") or "Unknown error")
        error_data = str(error_obj.get("data") or "")
        raise RuntimeError(f"{error_message}: {error_data}".strip())
    return parsed.get("result")


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _first_ip(interfaces: list[dict[str, object]]) -> str | None:
    for interface in interfaces:
        ip_value = interface.get("ip")
        if isinstance(ip_value, str) and ip_value.strip():
            return ip_value.strip()
    return None


def _resolve_host(config: RuntimeConfig) -> tuple[dict[str, object] | None, list[dict[str, object]], list[str]]:
    notes: list[str] = []
    if config.zabbix_hostid is not None:
        host_rows = _as_list(
            _rpc_call(
                config=config,
                method="host.get",
                params={
                    "output": ["hostid", "host", "name", "status"],
                    "selectInterfaces": ["ip", "main"],
                    "hostids": [config.zabbix_hostid],
                },
                request_id=1,
            )
        )
        if not host_rows:
            notes.append("host not found in zabbix")
            return None, [], notes
        host = _as_dict(host_rows[0])
        return host, [], notes

    if config.host_ip is None:
        notes.append("host_ip is required when zabbix_hostid is not provided")
        return None, [], notes

    interface_rows = _as_list(
        _rpc_call(
            config=config,
            method="hostinterface.get",
            params={"output": ["hostid", "ip", "main"], "filter": {"ip": [config.host_ip]}},
            request_id=2,
        )
    )
    if not interface_rows:
        notes.append("host not found in zabbix")
        return None, [], notes

    host_ids: list[str] = []
    for row in interface_rows:
        host_id = _as_dict(row).get("hostid")
        if isinstance(host_id, str) and host_id not in host_ids:
            host_ids.append(host_id)

    host_rows = [
        _as_dict(item)
        for item in _as_list(
            _rpc_call(
                config=config,
                method="host.get",
                params={
                    "output": ["hostid", "host", "name", "status"],
                    "selectInterfaces": ["ip", "main"],
                    "hostids": host_ids,
                },
                request_id=3,
            )
        )
    ]

    filtered_hosts = host_rows
    if config.host_name is not None:
        filtered_hosts = [
            host
            for host in host_rows
            if str(host.get("name") or "").strip() == config.host_name
            or str(host.get("host") or "").strip() == config.host_name
        ]

    if len(filtered_hosts) == 1:
        return filtered_hosts[0], [], notes

    if len(filtered_hosts) > 1:
        notes.append("ambiguous host ip")
        return None, filtered_hosts, notes

    if config.host_name is not None:
        notes.append("host_name did not match any host candidates")
        return None, host_rows, notes

    notes.append("ambiguous host ip")
    return None, host_rows, notes


def _fetch_problems(config: RuntimeConfig, hostid: str) -> list[dict[str, object]]:
    rows = _as_list(
        _rpc_call(
            config=config,
            method="problem.get",
            params={
                "output": ["eventid", "name", "severity", "clock"],
                "hostids": [hostid],
                "recent": True,
                "sortfield": ["eventid"],
                "sortorder": "DESC",
                "limit": 5,
            },
            request_id=4,
        )
    )
    return [_as_dict(item) for item in rows]


def _fetch_items(config: RuntimeConfig, hostid: str) -> list[dict[str, object]]:
    keys = [
        "system.cpu.util[,idle]",
        "system.cpu.load[all,avg1]",
        "vm.memory.size[pavailable]",
        "vfs.fs.size[/,pused]",
        "vfs.fs.size[/u01,pused]",
    ]
    rows = _as_list(
        _rpc_call(
            config=config,
            method="item.get",
            params={
                "output": ["itemid", "name", "key_", "lastvalue", "units", "status", "state"],
                "hostids": [hostid],
                "search": {"key_": keys},
                "searchByAny": True,
                "sortfield": "name",
                "limit": 50,
            },
            request_id=5,
        )
    )
    return [_as_dict(item) for item in rows]


def _safe_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _build_metrics(items: list[dict[str, object]]) -> tuple[dict[str, object], list[str], list[str]]:
    metrics: dict[str, object] = {}
    notes: list[str] = []
    history_item_ids: list[str] = []
    filesystems: list[dict[str, object]] = []

    for item in items:
        item_id = str(item.get("itemid") or "").strip()
        key_name = str(item.get("key_") or "").strip()
        last_value = _safe_float(item.get("lastvalue"))
        if not item_id:
            continue

        if key_name == "system.cpu.util[,idle]" and last_value is not None:
            metrics["cpu_idle_pct"] = round(last_value, 2)
            history_item_ids.append(item_id)
        elif key_name == "system.cpu.load[all,avg1]" and last_value is not None:
            metrics["load_avg_1m"] = round(last_value, 2)
        elif key_name == "vm.memory.size[pavailable]" and last_value is not None:
            metrics["memory_available_pct"] = round(last_value, 2)
            history_item_ids.append(item_id)
        elif key_name.startswith("vfs.fs.size[") and key_name.endswith(",pused]") and last_value is not None:
            mount = key_name[len("vfs.fs.size[") : -len(",pused]")]
            filesystems.append({"mount": mount, "used_pct": round(last_value, 2)})
            history_item_ids.append(item_id)

    if filesystems:
        metrics["filesystems"] = filesystems
    else:
        notes.append("filesystem usage items unavailable")

    if "cpu_idle_pct" not in metrics:
        notes.append("cpu idle metric unavailable")
    if "load_avg_1m" not in metrics:
        notes.append("load average metric unavailable")
    if "memory_available_pct" not in metrics:
        notes.append("memory available metric unavailable")

    return metrics, notes, history_item_ids


def _fetch_history(
    config: RuntimeConfig,
    item_ids: list[str],
) -> list[dict[str, object]]:
    if not item_ids:
        return []
    time_till = int(time.time())
    time_from = time_till - config.time_range_minutes * 60
    rows = _as_list(
        _rpc_call(
            config=config,
            method="history.get",
            params={
                "output": ["itemid", "clock", "value"],
                "history": 0,
                "itemids": item_ids,
                "time_from": time_from,
                "time_till": time_till,
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": 50,
            },
            request_id=6,
        )
    )
    return [_as_dict(item) for item in rows]


def _build_history_summary(history_rows: list[dict[str, object]], time_range_minutes: int) -> list[str]:
    by_item: dict[str, list[float]] = {}
    for row in history_rows:
        item_id = str(row.get("itemid") or "").strip()
        value = _safe_float(row.get("value"))
        if not item_id or value is None:
            continue
        by_item.setdefault(item_id, []).append(value)

    summaries: list[str] = []
    for values in by_item.values():
        latest = values[0]
        earliest = values[-1]
        delta = abs(latest - earliest)
        if delta < 0.5:
            summaries.append(f"过去{time_range_minutes}分钟相关指标整体稳定")
        else:
            summaries.append(f"过去{time_range_minutes}分钟相关指标存在波动")
        break
    return summaries


def run(config: RuntimeConfig) -> dict[str, object]:
    host, candidates, notes = _resolve_host(config=config)
    if host is None:
        result: dict[str, object] = {
            "target": None,
            "problems": [],
            "metrics": {},
            "history_summary": [],
            "notes": notes,
        }
        if candidates:
            result["candidate_hosts"] = [
                {
                    "hostid": str(candidate.get("hostid") or ""),
                    "host": str(candidate.get("host") or ""),
                    "name": str(candidate.get("name") or ""),
                    "ip": _first_ip([_as_dict(item) for item in _as_list(candidate.get("interfaces"))]),
                }
                for candidate in candidates
            ]
        return result

    hostid = str(host.get("hostid") or "").strip()
    problems = _fetch_problems(config=config, hostid=hostid)
    items = _fetch_items(config=config, hostid=hostid)
    metrics, metric_notes, history_item_ids = _build_metrics(items=items)
    history_rows = _fetch_history(config=config, item_ids=history_item_ids)
    history_summary = _build_history_summary(
        history_rows=history_rows,
        time_range_minutes=config.time_range_minutes,
    )

    target = {
        "hostid": hostid,
        "host": str(host.get("host") or ""),
        "name": str(host.get("name") or ""),
        "ip": _first_ip([_as_dict(item) for item in _as_list(host.get("interfaces"))]),
    }
    problem_rows = [
        {
            "eventid": str(problem.get("eventid") or ""),
            "name": str(problem.get("name") or ""),
            "severity": str(problem.get("severity") or ""),
        }
        for problem in problems
    ]
    return {
        "target": target,
        "problems": problem_rows,
        "metrics": metrics,
        "history_summary": history_summary,
        "notes": notes + metric_notes,
    }


def _parse_args() -> RuntimeConfig:
    parser = argparse.ArgumentParser(description="Query Zabbix host context in read-only mode.")
    parser.add_argument("--base-url", default=_env_or_default("ZABBIX_BASE_URL"))
    parser.add_argument("--api-token", default=_env_or_default("ZABBIX_API_TOKEN"))
    parser.add_argument("--host-ip", default=None)
    parser.add_argument("--host-name", default=None)
    parser.add_argument("--zabbix-hostid", default=None)
    parser.add_argument("--time-range-minutes", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=float, default=float(_env_or_default("ZABBIX_TIMEOUT_SECONDS", "8") or "8"))
    parser.add_argument("--verify-tls", default=_env_or_default("ZABBIX_VERIFY_TLS", "true"))
    args = parser.parse_args()

    if args.base_url is None or not str(args.base_url).strip():
        parser.error("base url is required")
    if args.api_token is None or not str(args.api_token).strip():
        parser.error("api token is required")

    return RuntimeConfig(
        base_url=str(args.base_url).strip(),
        api_token=str(args.api_token).strip(),
        host_ip=str(args.host_ip).strip() if args.host_ip is not None and str(args.host_ip).strip() else None,
        host_name=str(args.host_name).strip() if args.host_name is not None and str(args.host_name).strip() else None,
        zabbix_hostid=str(args.zabbix_hostid).strip() if args.zabbix_hostid is not None and str(args.zabbix_hostid).strip() else None,
        time_range_minutes=max(1, int(args.time_range_minutes)),
        timeout_seconds=max(1.0, float(args.timeout_seconds)),
        verify_tls=_bool_from_text(args.verify_tls, default=True),
    )


def main() -> int:
    config = _parse_args()
    try:
        result = run(config=config)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
