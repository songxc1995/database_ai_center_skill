#!/usr/bin/env python3
"""Small read-oriented client for Database AI Center DBA APIs."""

from __future__ import annotations

import argparse
import http.client
import json
from datetime import datetime, timedelta, timezone
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Keep the command path and direct importlib loading used by hosts; the sibling
# dba_client package is installed with the skill directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dba_client import alerts as _alerts
from dba_client import backups as _backups
from dba_client import core as _core
from dba_client import cost as _cost
from dba_client import diagnostics as _diagnostics
from dba_client import elk as _elk
from dba_client import inventory as _inventory
from dba_client import knowledge as _knowledge
from dba_client import metadata as _metadata


READ_TIMEOUT_DEFAULT = 15
ENV_FILE_NAMES = (".env",)
# One file, one location, on every OS: Path.home() is C:\\Users\\<name> on Windows and ~ on
# POSIX. It exists because the .env search walks up from the CURRENT WORKING DIRECTORY, and a
# host launched from Finder/Explorer has a cwd of "/" or the app bundle — no .env above it, and
# a GUI-launched process inherits almost nothing from the shell. Hosts that behave that way
# (WorkBuddy and whatever follows it) need a credential that does not depend on where the
# process happens to start.
USER_CONFIG_PATH_PARTS = (".dba-skill", "config")
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
_ENV_PROVENANCE: dict[str, Any] = {"source": None, "searched": [], "keys": [], "sources": {}}


def _credential_provenance() -> dict[str, Any]:
    """Where the credential came from — never what it is.

    Paths and names only. The key itself is stripped from every message by ``_redact``; a
    diagnostic that printed it would leak it into transcripts and screenshots, which is a far
    wider exposure than the file it sits in.
    """
    _load_env_files()
    source = _ENV_PROVENANCE.get("source")
    user_config = _user_config_path()
    out: dict[str, Any] = {
        "credential_source": source or "inherited process environment (no config file found)",
        "per_key_source": _ENV_PROVENANCE.get("sources") or {},
        "env_files_searched": _ENV_PROVENANCE.get("searched") or [],
        "user_config": {
            "path": str(user_config) if user_config else None,
            "exists": bool(user_config and user_config.is_file()),
        },
        "cwd": os.getcwd(),
    }
    return out


def _user_config_path() -> Path | None:
    """The per-user credential file, or None when the home directory is unavailable."""
    try:
        return Path.home().joinpath(*USER_CONFIG_PATH_PARTS)
    except (OSError, RuntimeError):
        return None


def _apply_env_file(path: Path, *, only_if_missing: bool = False) -> list[str]:
    """Load allowlisted KEY=value pairs from one file into ``os.environ``.

    Values go into the process environment rather than being returned, because ``_redact``
    finds the key there to strip it out of every error message. A parallel path that returned
    the key directly would leave redaction silently doing nothing.
    """
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return loaded
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or key not in ENV_ALLOWLIST:
            continue
        if only_if_missing and str(os.environ.get(key) or "").strip():
            continue
        os.environ[key] = _unquote_env_value(value)
        loaded.append(key)
    return loaded


def _load_env_files() -> None:
    """Resolve credentials, and record which source answered for each key.

    Precedence, lowest to highest: the per-user config file, the process environment, the
    nearest .env walking up from cwd.

    The existing .env-beats-environment order is deliberately left alone. It is there for a
    real failure — a stale key exported in a long-lived shell shadowing the fresh one next to
    the project you are actually in — and it is pinned by a test. The opposite failure has also
    happened here (a parent directory's viewer key shadowing a correctly configured ai-client
    key, whose 403 read as "revoked"), which is the point: whichever way precedence runs, two
    sources disagreeing is the problem, not the order. So the config file goes in at the bottom
    and nothing above it moves, and `per_key_source` says which file each key actually came
    from — the answer that ends the argument either way.
    """
    global _ENV_FILES_LOADED
    if _ENV_FILES_LOADED:
        return
    _ENV_FILES_LOADED = True

    sources: dict[str, str] = {
        key: "process environment"
        for key in ENV_ALLOWLIST
        if str(os.environ.get(key) or "").strip()
    }

    user_config = _user_config_path()
    if user_config is not None and user_config.is_file():
        # Bottom of the stack: only fills in what nothing else supplied. This is the path that
        # makes a Finder-launched host work at all — no cwd to search from, nothing inherited.
        for key in _apply_env_file(user_config, only_if_missing=True):
            sources[key] = str(user_config)
        if any(src == str(user_config) for src in sources.values()):
            _ENV_PROVENANCE["source"] = str(user_config)

    try:
        search_roots = (Path.cwd().resolve(), *Path.cwd().resolve().parents)
    except OSError:
        search_roots = ()
        _ENV_PROVENANCE["searched"] = ["<cwd unavailable>"]
    else:
        _ENV_PROVENANCE["searched"] = [str(d) for d in search_roots]

    for directory in search_roots:
        found = False
        for filename in ENV_FILE_NAMES:
            path = directory / filename
            if not path.is_file():
                continue
            for key in _apply_env_file(path):
                sources[key] = str(path)
                _ENV_PROVENANCE.setdefault("keys", []).append(key)
            _ENV_PROVENANCE["source"] = str(path)
            found = True
            break
        if found:
            break

    _ENV_PROVENANCE["sources"] = sources
    if not sources:
        _ENV_PROVENANCE["source"] = None
    elif _ENV_PROVENANCE.get("source") is None:
        _ENV_PROVENANCE["source"] = "process environment"


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
    global _LAST_FAILURE
    _LAST_FAILURE = _redact(message)
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
_LAST_FAILURE: str | None = None
# Pages --all will follow before giving up. A guard against an endpoint that never advances,
# not a preference — but it was invisible: not in SKILL.md, not in --help, only in the source,
# so hitting it looked like "that is all the data there is".
_MAX_PAGES = 50
_COUNT_ONLY = False
_SUMMARY_ONLY = False

# Last HTTP status seen, so the /dba prefix retry fires only for a genuine 404. A 401 is not
# a path problem: retrying it just emits a second identical failure and buries the first.
#: 最后一次 HTTP 错误的正文。有些 4xx 的正文**就是答案** —— 平台用 422 说明「这个问题在
#: 这个窗口上没有答案」,只留状态码会把答案降级成报错。
_LAST_HTTP_BODY: str | None = None
_LAST_HTTP_STATUS: int | None = None

# Parts that could not be read during this run. A composite answer built on an unreadable
# dependency is not a complete answer, and saying so is the whole difference between
# "this instance has no owner" and "we could not ask".
_DEGRADED: list[dict[str, Any]] = []
# Fleet-wide payloads reused across a fan-out; see _try_get_shared. Entries carry a
# timestamp: a CLI run lasts seconds, but this module can also be imported by a long-lived
# host, and a cache with no expiry would keep answering that question with this morning's
# coverage table. Long enough to cover a fan-out over the whole fleet, short enough that
# nobody acts on a stale answer.
_SHARED_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_SHARED_CACHE_TTL_SECONDS = 120.0


def _request(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
    """Every call goes through here: pages when asked, and always says when a page is partial."""
    if method == "GET" and _FETCH_ALL:
        payload = _fetch_all(method, path, dict(params or {}))
        # Same warning as the single-page path. Skipping it here was worse than not having it:
        # without --all the caller at least knows they asked for one page, while --all promises
        # to follow pagination to the end and then quietly stopped at 12% of the rows.
        _warn_if_truncated(payload, path, request_params=params)
        return payload
    payload = _request_once(method, path, params=params, body=body)
    if method == "GET":
        _warn_if_truncated(payload, path, request_params=params)
    return payload


# Every key the platform puts a collection under. SKILL.md has listed all of these for a
# while; this function implemented two of them, so --sort-by and --group-by silently did
# nothing on probe-run (rows), elk-search (hits), timeline (events) and the rest — exactly the
# places where sorting matters most: segments by size, slow SQL by time, logs by level.
# Order is priority: a response carrying more than one list is read as the first match.
COLLECTION_KEYS: tuple[str, ...] = (
    "items", "rows", "instances", "events", "entries", "results", "hits", "probes", "matches",
)


def _envelope(payload: Any) -> tuple[list[Any] | None, dict[str, Any]]:
    """Split a response into (items, meta) across every shape the platform returns.

    There is no single envelope: `ai-endpoints` and `metrics/{id}/latest` return a bare list;
    `instances` returns {items, total, limit, offset, truncated}; `alerts` returns
    {items, total, page, page_size, has_next}; probes return {rows, ...}; ELK returns {hits};
    the knowledge base returns {entries} and {results}. Every consumer otherwise has to probe
    defensively, and guessing wrong turns into 'str' object has no attribute 'get' at the
    worst moment.

    `meta["items_key"]` names the key the rows came from, so anything that rewrites the
    collection can put it back where it belongs instead of guessing.

    Returns (None, {}) for a single object, which is not a collection and must not be
    silently treated as one.
    """
    if isinstance(payload, list):
        return payload, {"shape": "list", "total": len(payload)}
    if isinstance(payload, dict):
        for key in COLLECTION_KEYS:
            if isinstance(payload.get(key), list):
                meta = {k: v for k, v in payload.items() if k != key}
                meta["shape"] = "envelope"
                meta["items_key"] = key
                return payload[key], meta
    return None, {}


def _warn_if_truncated(
    payload: Any,
    path: str,
    *,
    request_params: dict[str, Any] | None = None,
) -> None:
    """Say so on stderr when a page is not the whole answer.

    A truncated page and a complete one are the same shape, so "no rows matched" and "your
    row is on page 2" are indistinguishable without reading `total`. Production 2026-09-01: a
    caller asked which instances had no databases, got 2000 of 2072 rows, and the two
    instances it was looking for were in the missing 72.
    """
    if not isinstance(payload, dict):
        return
    if _COUNT_ONLY or _SUMMARY_ONLY:
        # The caller asked for the counts and is getting them in full. Telling them the rows
        # are incomplete — and to fetch more rows — argues against what they just requested.
        return
    items, meta = _envelope(payload)
    if items is None:
        return
    total = meta.get("total")
    if meta.get("truncated") is True or (isinstance(total, int) and total > len(items)):
        # The advice has to match what the caller already did. Telling someone who passed
        # --all to "re-run with --all" is how a warning trains people to ignore warnings.
        # ★ ...and it has to be advice that works. --all only pages envelopes that carry
        #   offset/limit or page/page_size; for the rest (e.g. `alerts` → /dba/alerts) it made no
        #   further request, yet this said "stopped at the page guard" and offered
        #   `--param limit=1000` — a usage error on that command (2026-09-15 eval).
        pages = any(k in meta for k in ("offset", "limit", "page", "page_size")) or any(
            k in (request_params or {}) for k in ("offset", "limit", "page", "page_size")
        )
        if _FETCH_ALL and pages:
            advice = (
                f"--all stopped at the {_MAX_PAGES}-page guard. Raise it with --max-pages N, "
                f"ask for bigger pages with the command's --limit / --page-size, or narrow the "
                f"query. Use --count-only when you just need the total."
            )
        elif _FETCH_ALL:
            advice = (
                "this endpoint does not page, so --all had nothing more to fetch. Raise the "
                "command's --limit (the endpoint may cap it) or narrow the filters."
            )
        elif pages:
            advice = "re-run with --all, or page with offset/page."
        else:
            advice = "this endpoint does not page: raise the command's --limit or narrow the filters."
        sys.stderr.write(
            json.dumps(
                {
                    "warning": "partial_result",
                    "message": (
                        f"{path} returned {len(items)} of {total} rows. This page is NOT the "
                        f"whole answer — {advice}"
                    ),
                    "returned": len(items),
                    "total": total,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _fetch_all(method: str, path: str, params: dict[str, Any], *, page_limit: int | None = None) -> Any:
    """Follow pagination to the end, for both vocabularies the platform uses.

    ``page_limit`` is a guard, not a preference: an endpoint that never advances would
    otherwise loop forever, and a silent infinite loop is worse than a partial answer that
    says it is partial — which is exactly what this used to do. The guard tripped at 50 pages
    and returned 10,200 of 84,715 rows with an empty stderr, because ``--all`` returned before
    reaching the truncation warning. Saying "partial" is the whole point of stopping this way,
    so the caller now hears it, and ``--max-pages`` raises the ceiling when more is wanted.
    """
    if page_limit is None:
        page_limit = _MAX_PAGES
    first = _request_once(method, path, params=dict(params))
    items, meta = _envelope(first)
    if items is None or meta.get("shape") != "envelope":
        return first

    collected = list(items)
    total = meta.get("total")
    if not isinstance(total, int):
        return first

    if "offset" in meta or "limit" in meta or ("offset" in params and "limit" in params):
        size = int(meta.get("limit") or params.get("limit") or len(items) or 1)
        pages = 0
        while len(collected) < total and pages < page_limit and size > 0:
            pages += 1
            nxt = _request_once(method, path, params={**params, "limit": size, "offset": len(collected)})
            more, _ = _envelope(nxt)
            if not more:
                break
            collected.extend(more)
    elif "page" in meta or "page_size" in meta or ("page" in params and "page_size" in params):
        size = int(meta.get("page_size") or params.get("page_size") or len(items) or 1)
        page = int(meta.get("page") or params.get("page") or 1)
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


_MISSING = object()


def _dig(row: Any, path: str) -> Any:
    """Follow a dotted path into a row, or return _MISSING when it does not exist.

    The backup-coverage rows keep nearly everything worth reading inside the ``local`` and
    ``remote`` objects, so a flat lookup left the caller two bad choices: dump the whole
    nested blob into a table cell, or ask for ``local.status`` and get a blank column. A blank
    column is the worse one — it reads as "these instances have no data".
    """
    current = row
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _counts_only(payload: Any) -> Any:
    """The envelope without its rows.

    "How many data-quality findings are there" should not cost 84,715 rows through the context
    window to answer. Everything except the collection is kept, so `total`, `truncated` and any
    per-verdict counts still come back.
    """
    if not isinstance(payload, dict):
        return payload
    items, meta = _envelope(payload)
    if items is None:
        return payload
    # Any list is a collection here; dropping them all covers every envelope vocabulary
    # (items / instances / events / probes / entries / results / hits) without naming them.
    out = {key: value for key, value in payload.items() if not isinstance(value, list)}
    out["returned_rows"] = len(items)
    out["rows_omitted_by"] = "--count-only"
    return out


#: 只影响**怎么输出**、不影响**问了什么**的旗标。回显过滤条件时要排掉它们。
_OUTPUT_ONLY_DESTS = {
    "all", "max_pages", "snapshot", "since", "only_if_changed", "group_by", "sort_by",
    "desc", "count_only", "summary_only", "fields", "format", "func", "command",
    "_needs_instance", "refresh",
}


def _applied_filters(args: argparse.Namespace) -> dict[str, Any]:
    """这一次实际带上的过滤条件。

    ``alerts --severity nosuch`` 返回的是 ``{"counts": {全 0}, "items": [], "total": 0}`` ——
    和"确实没有告警"**一个字都不差**。打错一个词表值,换来的是一个理直气壮的 0。

    有限词表(如 ``--severity``)加 ``choices`` 就能在参数解析时响掉;但 ``--environment`` /
    ``--metric-name`` / ``--levels`` 这些是**部署相关或会增长的**,硬编码 choices 会拒掉合法的
    新值。对它们,正确做法是把条件回显出来:"我按 X 过滤,得到 0 条"和"总共就是 0 条"于是
    可区分。平台侧 ``databases-search`` 早就这么做了(返回 ``filters``),这里把同一件事补给
    其余命令。
    """
    out: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in _OUTPUT_ONLY_DESTS or key.startswith("_"):
            continue
        if value is None or value is False or value == []:
            continue
        out[key] = value
    return out


def _lists_present(payload: Any) -> list[str]:
    """这个响应里**实际**存在的列表键(顶层,含一层嵌套)。

    没有它,拒绝信息只能说"Collections arrive under: items, rows, …" —— 那是一份**别处**的
    名单,对着一个有三个列表(months / years / year_on_year)的响应说"this response is a
    single object",读起来像"这儿没东西可分组",而真相是"有三个,但一个都不在识别名单里"。
    """
    found: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, list) and v:
                found.append(k)
            elif isinstance(v, dict):
                found.extend("%s.%s" % (k, k2) for k2, v2 in v.items()
                             if isinstance(v2, list) and v2)
    return found


def _not_a_collection(flag: str) -> str:
    """拒绝一个需要集合的旗标时该说的话。"""
    return ("%s needs a collection; this response is a single object. "
            "Collections arrive under: %s." % (flag, ", ".join(COLLECTION_KEYS)))


def _summary_only(payload: Any) -> Any:
    """Every list, at any depth, replaced by its length.

    ``--count-only`` drops only the *top-level* collection, which was enough when an envelope
    was `{items: [...], total: N}`. It stopped being enough once headline objects started
    carrying their own lists: `cloud-rightsizing --count-only` still returns ~10 KB because
    `coupon_funded.instances` (37) and `storage_summary.over_provisioned` (20) are nested
    inside dicts and never matched the top-level filter. Same response under this flag: 2 KB.

    The length is kept rather than the key dropped — "37 instances are coupon-funded" is the
    part worth having, and a vanished key would read as "there are none".
    """
    if isinstance(payload, dict):
        return {k: _summary_only(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return {"count": len(payload), "omitted_by": "--summary-only"}
    return payload


def _project(payload: Any, fields: list[str] | None) -> Any:
    """Keep only the requested fields, so a 400 KB dump can be four columns.

    Supports dotted paths (``local.status``). A field that matches nothing on any row is an
    error rather than an empty column: asking for something that is not there is a mistake in
    the request, and answering it with blanks makes the mistake look like data.
    """
    if not fields:
        return payload
    items, meta = _envelope(payload)
    if items is None:
        if not isinstance(payload, dict):
            return payload
        picked_one = {key: _dig(payload, key) for key in fields}
        missing = [k for k, v in picked_one.items() if v is _MISSING]
        if missing:
            raise SystemExit(
                f"--fields: no such field(s): {', '.join(missing)}. "
                f"Available: {', '.join(sorted(payload)[:20])}"
            )
        return picked_one
    dict_rows = [row for row in items if isinstance(row, dict)]
    unmatched = [
        key for key in fields
        if dict_rows and all(_dig(row, key) is _MISSING for row in dict_rows)
    ]
    if unmatched:
        sample = dict_rows[0]
        available = sorted(sample)
        nested = [
            f"{k}.{sub}" for k, v in sample.items() if isinstance(v, dict) for sub in sorted(v)
        ]
        # --instance-ids wraps each answer as {instance_id, result}, so every field name that
        # works for one instance needs a `result.` in front of it for a batch. Saying which
        # prefix would have worked beats leaving the reader to infer it from a field list.
        rebased = [
            f"{key} → result.{key}" for key in unmatched
            if not key.startswith("result.")
            and any(_dig(row, f"result.{key}") is not _MISSING for row in dict_rows)
        ]
        raise SystemExit(
            f"--fields: no such field(s): {', '.join(unmatched)}. "
            + (f"In batch mode each row is {{instance_id, result}} — try: "
               f"{', '.join(rebased)}. " if rebased else "")
            + f"Available: {', '.join(available)}"
            + (f"; nested: {', '.join(nested[:20])}" if nested else "")
        )
    picked = [
        {key: (None if (v := _dig(row, key)) is _MISSING else v) for key in fields}
        if isinstance(row, dict) else row
        for row in items
    ]
    if meta.get("shape") == "list":
        return picked
    out = {key: value for key, value in payload.items() if key != "items"}
    out["items"] = picked
    return out


SNAPSHOT_DIR_PARTS = (".dba-skill", "snapshots")
# Fields whose change is almost always the platform re-deciding rather than the estate moving.
# Not hidden — labelled, so a reader can tell "this database lost its backup" from "we changed
# how backups are judged".
_JUDGEMENT_FIELDS = frozenset({"verdict", "underlying_verdict", "determination", "status",
                               "grade", "severity", "applicable", "suppressed_by"})
# Values derived from "now", which therefore differ on every single call. Two runs seconds
# apart reported 48 changed rows, all of them an age counter ticking — noise that buries the
# handful of rows something actually happened to. Dropped by name, and the diff says which
# names were dropped: silently ignoring fields is how a diff starts lying by omission.
_VOLATILE_FIELDS = frozenset({
    "generated_at", "current_at", "uptime_seconds", "started_at", "trace_id",
    "sync_age_seconds", "age_seconds", "seconds_since", "last_seen_seconds_ago",
})


def _strip_volatile(value: Any) -> Any:
    """A row with its clock-derived fields removed, at any depth."""
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_FIELDS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _fan_out(args: argparse.Namespace, ids: list[int]) -> dict[str, Any]:
    """Run the same per-instance command for several instances, and account for every one.

    Replaces the shell loop this was written as seventeen times in one week — seven process
    starts, seven credential resolutions, seven fragments of parsing. The reason it is not a
    plain list comprehension is the rate limit: live-database reads are capped at 30/minute
    and 10/minute per instance, so a wide fan-out will have some calls rejected. Dropping
    those would hand back a short list shaped exactly like a complete one, which is the defect
    this client keeps having to unlearn — so every requested id ends up in `results` or in
    `failed`, and `partial` is true whenever anything failed.
    """
    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for instance_id in ids:
        scoped = argparse.Namespace(**vars(args))
        scoped.instance_id = instance_id
        before = len(_DEGRADED)
        try:
            payload = args.func(scoped)
        except SystemExit as exc:
            failed.append({"instance_id": instance_id,
                           "error": _LAST_FAILURE or f"exit {exc.code}"})
            continue
        except Exception as exc:  # noqa: BLE001 - one instance failing must not end the run
            failed.append({"instance_id": instance_id, "error": _short_reason(exc)})
            continue
        entry: dict[str, Any] = {"instance_id": instance_id, "result": payload}
        # The command returned, but possibly on an incomplete reading. Reported here because
        # `returned` and `failed` both said everything was fine while a throttled dependency
        # was quietly turning "could not ask" into "confirmed missing". Two signals, because
        # either alone has a blind spot: the request layer knows which call failed, and the
        # command knows which of its answers that cost it.
        degraded_parts: list[dict[str, Any]] = list(_DEGRADED[before:])
        if isinstance(payload, dict):
            degraded_parts += [{"check": name} for name in (payload.get("unavailable") or [])]
        if degraded_parts:
            entry["degraded"] = degraded_parts
        results.append(entry)

    degraded = [r for r in results if r.get("degraded")]
    out: dict[str, Any] = {
        "requested": ids,
        "returned": len(results),
        "items": results,
        "partial": bool(failed) or bool(degraded),
    }
    if degraded:
        out["degraded_instances"] = [r["instance_id"] for r in degraded]
        sys.stderr.write(json.dumps({
            "warning": "degraded_result",
            "message": (
                f"{len(degraded)} of {len(ids)} instances answered from an incomplete "
                f"reading (a dependency could not be fetched). Their unanswered checks are "
                f"reported as `unknown`, not as gaps. See `degraded` on each item."
            ),
        }, ensure_ascii=False) + "\n")
    if failed:
        out["failed"] = failed
        sys.stderr.write(json.dumps({
            "warning": "partial_result",
            "message": (
                f"{len(failed)} of {len(ids)} instances did not answer "
                f"({', '.join(str(f['instance_id']) for f in failed)}). See `failed` for why. "
                f"Live-database reads are capped at 30/minute and 10/minute per instance."
            ),
        }, ensure_ascii=False) + "\n")
    return out


def _parse_instance_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw.replace(" ", "").split(","):
        if not chunk:
            continue
        if not chunk.isdigit():
            _fail("invalid_instance_ids", f"--instance-ids takes a comma-separated list of "
                                          f"numbers; got {chunk!r}", exit_code=2)
        value = int(chunk)
        if value not in ids:
            ids.append(value)
    if not ids:
        _fail("invalid_instance_ids", "--instance-ids was empty", exit_code=2)
    return ids


def _platform_version() -> str | None:
    """Which release answered, so a diff can tell a redefinition from a real change."""
    try:
        payload = _request_once("GET", "/observability/version")
    except Exception:  # noqa: BLE001 - version is context, never the answer itself
        return None
    return str(payload.get("version")) if isinstance(payload, dict) else None


def _snapshot_dir() -> Path | None:
    try:
        return Path.home().joinpath(*SNAPSHOT_DIR_PARTS)
    except (OSError, RuntimeError):
        return None


def _snapshot_fingerprint(argv: list[str]) -> str:
    """Which question this snapshot answers.

    Diffing two different questions produces confident nonsense, so a snapshot is only ever
    compared with one taken by the same command and parameters. Output-shaping flags are
    excluded — --fields or --format change the rendering, not the question.
    """
    import hashlib

    # Everything that shapes the OUTPUT rather than the question. Leaving one out silently
    # splits the fingerprint, so a snapshot taken one way never matches a diff asked for
    # another way — which surfaces as "no snapshot" for a snapshot that plainly exists.
    skip_with_value = {"--fields", "--format", "--sort-by", "--group-by", "--since",
                       "--max-pages"}
    skip_flags = {"--snapshot", "--desc", "--count-only", "--summary-only", "--all",
                  "--only-if-changed"}
    parts, i = [], 0
    while i < len(argv):
        token = argv[i]
        if token in skip_with_value:
            i += 2
            continue
        if token in skip_flags or token.split("=", 1)[0] in skip_with_value:
            i += 1
            continue
        parts.append(token)
        i += 1
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()[:16]


def _row_identity(row: Any) -> str | None:
    """A stable name for one row, or None when it has none.

    Without an identity two collections can only be compared by size, which answers "how many"
    and never "which". Rows with no id are reported as uncomparable rather than matched by
    position — position is not identity, and pretending it is manufactures changes.
    """
    if not isinstance(row, dict):
        return None
    for key in ("instance_id", "id", "alert_id", "database_id", "instance_name", "value"):
        if row.get(key) is not None:
            return f"{key}={row[key]}"
    return None


def _write_snapshot(payload: Any, argv: list[str], platform_version: str | None) -> str | None:
    directory = _snapshot_dir()
    if directory is None:
        return None
    directory = directory / _snapshot_fingerprint(argv)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps({
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "command": argv,
        "platform_version": platform_version,
        "payload": payload,
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _parse_since(value: str) -> datetime | None:
    """`last`, `yesterday`, `3d`, `6h`, or an ISO timestamp."""
    text = value.strip().lower()
    now = datetime.now(timezone.utc)
    if text in ("last", "previous"):
        return now
    if text == "yesterday":
        return now - timedelta(days=1)
    if text == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if text.endswith("h") and text[:-1].isdigit():
        return now - timedelta(hours=int(text[:-1]))
    if text.endswith("d") and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_snapshot(argv: list[str], since: datetime) -> dict[str, Any] | None:
    """The newest snapshot at or before `since`, for this exact question."""
    directory = _snapshot_dir()
    if directory is None:
        return None
    directory = directory / _snapshot_fingerprint(argv)
    if not directory.is_dir():
        return None
    best: tuple[datetime, dict[str, Any]] | None = None
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            taken = datetime.fromisoformat(str(data.get("taken_at")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        if taken <= since and (best is None or taken > best[0]):
            best = (taken, {**data, "path": str(path)})
    return best[1] if best else None


def _diff_payloads(before: Any, after: Any) -> dict[str, Any]:
    """What changed between two answers to the same question.

    Rows are matched by identity, never by position. Anything without an identity is reported
    under `uncomparable` instead of being paired up by where it happened to sit — inventing a
    change is worse than admitting the rows cannot be tracked.
    """
    old_items, _ = _envelope(before)
    new_items, _ = _envelope(after)
    old_items = old_items or []
    new_items = new_items or []

    old_by, new_by, uncomparable = {}, {}, 0
    for row in old_items:
        key = _row_identity(row)
        if key is None:
            uncomparable += 1
        else:
            old_by[key] = row
    for row in new_items:
        key = _row_identity(row)
        if key is None:
            uncomparable += 1
        else:
            new_by[key] = row

    added = [new_by[k] for k in new_by.keys() - old_by.keys()]
    removed = [old_by[k] for k in old_by.keys() - new_by.keys()]
    changed = []
    for key in old_by.keys() & new_by.keys():
        old_row, new_row = old_by[key], new_by[key]
        stripped_old, stripped_new = _strip_volatile(old_row), _strip_volatile(new_row)
        fields = {
            field: {"from": stripped_old.get(field), "to": stripped_new.get(field)}
            for field in set(stripped_old) | set(stripped_new)
            if stripped_old.get(field) != stripped_new.get(field)
        }
        if fields:
            changed.append({"identity": key, "fields": fields})

    return {
        "added": added,
        "removed": removed,
        "changed": sorted(changed, key=lambda c: c["identity"]),
        "unchanged": len(old_by.keys() & new_by.keys()) - len(changed),
        "uncomparable_rows": uncomparable,
        "ignored_fields": sorted(_VOLATILE_FIELDS),
    }


def _annotate_judgement_changes(diff: dict[str, Any], before_version: str | None,
                                after_version: str | None) -> dict[str, Any]:
    """Separate "the estate moved" from "we changed our mind about it".

    Three production instances changed verdict between two runs five hours apart. Nothing
    happened to those databases — a release had changed how the verdict is computed. A diff
    that reports both the same way trains its reader to skim past it, which is the failure
    mode this whole feature exists to avoid.
    """
    if before_version and after_version and before_version != after_version:
        judgement_only = [
            c for c in diff["changed"]
            if set(c["fields"]) and set(c["fields"]) <= _JUDGEMENT_FIELDS
        ]
        if judgement_only:
            diff["platform_version_changed"] = f"{before_version} → {after_version}"
            diff["possibly_judgement_not_estate"] = [c["identity"] for c in judgement_only]
            diff["note"] = (
                f"The platform moved from {before_version} to {after_version} between these "
                f"two snapshots. {len(judgement_only)} row(s) changed only in fields the "
                f"platform computes, so the verdict may have been redefined rather than the "
                f"database changing. Check the release notes before acting on those."
            )
    return diff


def _sort_rows(payload: Any, sort_by: str | None, descending: bool) -> Any:
    """Order a collection by one field, dotted paths included.

    Rows that lack the field sort last in both directions rather than crashing or silently
    becoming zero — "this row has no size" and "this row is the smallest" are different facts.
    """
    if not sort_by:
        return payload
    items, meta = _envelope(payload)
    if items is None:
        # Same treatment --format csv already gives this case. Returning the payload untouched
        # meant --sort-by on a single object exited 0 with byte-identical output and an empty
        # stderr: the flag did nothing and said nothing.
        _lists = _lists_present(payload)
        _fail("not_a_collection",
              _not_a_collection("--sort-by")
              + (" This response does carry list(s) under %s — none is the response's"
                 " collection, so there is no single one to use; project with --fields,"
                 " or read it as JSON." % ", ".join(_lists) if _lists else ""), exit_code=2)
    missing = object()

    def key(row: Any):
        value = _dig(row, sort_by) if isinstance(row, dict) else _MISSING
        if value is _MISSING or value is None:
            return (1, 0, "")
        if isinstance(value, bool):
            return (0, int(value), "")
        if isinstance(value, (int, float)):
            return (0, value, "")
        # Numbers arrive as strings often enough that ignoring it is not an option: Oracle
        # probes return size_gb as "109.67", and comparing those as text puts "44.82" above
        # "109.67". A wrong order is worse than no order — it looks like an answer.
        text = str(value)
        try:
            return (0, float(text), "")
        except ValueError:
            return (0, 0, text)

    ordered = sorted(items, key=key, reverse=descending)
    # Rows without the field stay at the end whichever way the rest is sorted.
    if descending:
        ordered = [r for r in ordered if key(r)[0] == 0] + [r for r in ordered if key(r)[0] == 1]
    return _replace_items(payload, ordered, meta)


def _group_rows(payload: Any, group_by: str) -> Any:
    """Count rows per distinct value of one field — the `Counter(...)` written by hand 95 times.

    Returns counts, not the rows: the question "how many of each" does not need the rows, and
    the ones that do are served by --fields. Rows missing the field are counted under
    `(missing)` rather than dropped, because a silent shrink in the denominator is how a
    grouped answer starts lying.
    """
    items, _meta = _envelope(payload)
    if items is None:
        _lists = _lists_present(payload)
        _fail("not_a_collection",
              _not_a_collection("--group-by")
              + (" This response does carry list(s) under %s — none is the response's"
                 " collection, so there is no single one to use; project with --fields,"
                 " or read it as JSON." % ", ".join(_lists) if _lists else ""), exit_code=2)
    counts: dict[str, int] = {}
    for row in items:
        value = _dig(row, group_by) if isinstance(row, dict) else _MISSING
        if value is _MISSING:
            label = "(missing)"
        elif isinstance(value, list):
            label = ", ".join(str(v) for v in value) or "(empty)"
        else:
            label = "(null)" if value is None else str(value)
        counts[label] = counts.get(label, 0) + 1
    # Under `items`, the canonical collection key, so --format table/csv and every other
    # downstream step treat a grouped result like any other collection instead of falling
    # back to raw JSON.
    return {
        "group_by": group_by,
        "rows_grouped": len(items),
        "items": [{"value": k, "count": v}
                  for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def _replace_items(payload: Any, rows: list[Any], meta: dict[str, Any]) -> Any:
    """Put a reordered collection back under the key it came from.

    Matching by list length instead would silently rewrite the wrong field whenever a response
    carries two lists of equal size.
    """
    if meta.get("shape") == "list" or not isinstance(payload, dict):
        return rows
    key = meta.get("items_key")
    return {**payload, key: rows} if key else payload


def _to_csv(payload: Any) -> str | None:
    """Render a collection as CSV, or None when it is not tabular.

    `--format table` is for reading and `json` is for programs; the list that has to reach a
    database owner by mail lands in neither. Columns are the union of the rows' keys, in the
    order first seen, so a projection with --fields keeps that order.
    """
    import csv as _csv
    import io

    items, _meta = _envelope(payload)
    if not items or not all(isinstance(row, dict) for row in items):
        return None
    columns: list[str] = []
    for row in items:
        for key in row:
            if key not in columns:
                columns.append(key)
    buffer = io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in items:
        writer.writerow({c: _csv_cell(row.get(c)) for c in columns})
    return buffer.getvalue()


def _csv_cell(value: Any) -> str:
    """A cell a spreadsheet can use.

    `_cell` renders a list as JSON and truncates at 60 characters — right for a terminal
    table, wrong for a file someone opens in Excel and mails on: ["张三", "李四"] should
    read as a list of names, and a name must not be cut in half at the 60th character.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_csv_cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ★ 出站脱敏。SKILL.md 的 Safety Rules 写着不得输出用户名/口令/密钥/连接串,
# 但那条规则此前**没有任何东西在执行它** —— `instance --instance-id 19` 今天就在
# 吐 `instance.username = dbai_mon`(admin 与 ai-client 两种角色实测都吐)。
#
# 为什么做成收口处的黑名单而不是"我核过这个端点的输出没问题":后者是白名单思维的
# 另一种写法,只能覆盖我当时看过的那几条命令(同伴扫了 10/37 条就发现了一处)。
# 平台哪天在别的端点上多带一个 username,逐端点核查这套办法不会有任何反应。
_SENSITIVE_KEYS = frozenset({
    "username", "user_name", "db_user", "login", "password", "passwd", "pwd",
    "secret", "token", "access_token", "api_key", "apikey", "private_key",
    "dsn", "connection_string", "conn_string",
})
# ★ **不要往这个名单里加 `owner` / `object_name`。** Oracle 有按 schema 属主的空间探针
#   (`owner_top_segments`、`tablespace_segments_by_owner`),实跑 inst35 确认:属主是**输入参数**
#   (`param_kind: "owner"`),结果列是 segment_name / segment_type / size_gb / tablespace_name。
#   加 `object_name` 会把 `applied_filters.object_name` 遮掉 —— 那是"这次查的是谁的段",
#   遮了报表就没法回答自己在说哪个属主。schema 属主是正当诊断数据,不是登录账号。
#   (`login` / `db_user` 实测在 23 个端点的 495 个字段名里零占用,留着不花成本。)
_SENSITIVE_SUFFIXES = ("_password", "_passwd", "_secret", "_token", "_api_key", "_apikey")
_REDACTED = "<redacted-by-dba-skill>"

# ★ 按键名遮蔽有一处会误伤:`credentials.per_key_source` 是**以变量名为键**的映射,
#   键叫 PROJECT_API_KEY,值却是"这把 key 是从哪儿读到的"(`process environment` 或某个
#   .env 的路径)。遮掉它等于把排查"到底哪把 key 在生效"唯一有用的字段抹了 ——
#   而那正是上次「陈旧已吊销 key 污染每个 shell」查了半天的那个字段。
#   豁免的依据不是"我信任这个端点",是**这段值由本文件自己构造**(cmd_whoami 里),
#   取值只有固定几种措辞和文件路径,真正的 key 从不放进去。
#   只豁免这一层父路径(不是前缀):再往下如果哪天多出嵌套,照样遮。
_REDACTION_EXEMPT_PARENTS = frozenset({"credentials.per_key_source"})

# ★ 值兜底分两层,分界线是**匹配粒度**而不是长度 —— 长度这个轴分不开真正危险的那种 key。
#   实测(11 条命令 / 514KB 真实输出 / 24,995 个字符串值):
#
#       随机串碰撞   4 位 7.8% → 5 位 0.2% → **6 位起 0%**
#       词形串       root 子串命中 27、prd 588、ha 948 —— 而**整值命中全是 0**
#
#   也就是说:随机 key 6 位就安全,而 `prd` 这种 3 位词形 key 撞 588 次。同样长度、
#   碰撞率差几个数量级,所以"设个最短长度"拦不住我原本担心的那种 key。整值匹配才拦得住。
#
#   第一层 整值相等  → 一律遮,不设门槛。短 key 也照样受保护,且实测零误遮。
#   第二层 子串命中  → 仅当 key 长度 ≥ 门槛。这层不能省:key 嵌在连接串
#                     `postgres://u:KEY@host` 或 `?api_key=` 里时,整值匹配看不见。
#   门槛取 12:本部署真实 key 是 36 / 48 位(两把都远在门槛之上,不会被降级),
#   而 12 位以上的词形串撞上无关输出的可能性已经可以忽略。
_KEY_SUBSTRING_MIN = 12
_SHORT_KEY_ANNOUNCED = False


def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    return low in _SENSITIVE_KEYS or low.endswith(_SENSITIVE_SUFFIXES)


def _leaks(key: str, value: Any, parent_path: str) -> bool:
    """这一格该不该遮。

    两条判据,第二条是兜底:键名像敏感字段(除非父路径在豁免名单里),**或者**值本身
    逐字节等于当前这把 API key —— 后者不看字段叫什么,所以平台哪天把 key 回显在一个
    叫 `note` 的字段里也拦得住。按名字遮永远只能拦住起对了名字的那些。
    """
    if value is None:
        return False
    if _is_sensitive_key(key) and parent_path not in _REDACTION_EXEMPT_PARENTS:
        return True
    live = os.environ.get("PROJECT_API_KEY", "").strip()
    if not live or not isinstance(value, str):
        return False
    if value.strip() == live:
        return True                      # 第一层:整值,无门槛
    # ★ **不要在这里加 `len(value) >= len(live)` 之类的前置判断。** 看着像是能跳过绝大多数值,
    #   实测反而**慢 2.2–2.4 倍**(真实语料,三种 key 长度结论一致):
    #
    #       12 位 key  直接 in 0.16ms | len 前置 0.34ms | 2.16×   (短于 key 的值占 53%)
    #       48 位 key  直接 in 0.10ms | len 前置 0.22ms | 2.35×   (短于 key 的值占 95%)
    #
    #   ★ 48 位那行最说明问题:能"跳过"的比例高到 95%,它依然慢 —— 因为 `str.__contains__`
    #   是 C 实现、**内部本来就先比长度**,短于针的字符串在 C 层直接返回。在外面再比一次,
    #   等于把 C 里一条指令的事搬进解释器,每个值多付一次函数调用。想省的那步已经在更快的地方做过了。
    #   顺带:脱敏本身不是瓶颈 —— 最大载荷(databases-search --all,13,264 个字符串 / 667KB)
    #   实测 24ms,而取这份数据要几百毫秒到几秒。
    return len(live) >= _KEY_SUBSTRING_MIN and live in value   # 第二层:子串,有门槛


def _announce_substring_layer_downgrade() -> None:
    """key 太短导致子串那层关掉时,说一次。

    ★ 我自己写过"遮蔽过宽和过窄一样是缺陷,过宽的更难发现";**静默降级比静默过宽更危险** ——
    过宽至少在输出里留下了标记,而降级什么痕迹都没有:遮蔽看起来仍在工作,只是少了一层。
    说出来,它就不再是静默的了。
    """
    global _SHORT_KEY_ANNOUNCED
    if _SHORT_KEY_ANNOUNCED:
        return
    live = os.environ.get("PROJECT_API_KEY", "").strip()
    if live and len(live) < _KEY_SUBSTRING_MIN:
        _SHORT_KEY_ANNOUNCED = True
        sys.stderr.write(
            "[redacted] ★ 当前 PROJECT_API_KEY 只有 %d 位(门槛 %d),子串兜底这一层已关闭:"
            "整值相等仍然会遮,但 key 嵌在连接串或 URL 里时不会被认出来。"
            "短 key 拿它去撞正常输出的误遮率太高,不能开。\n"
            % (len(live), _KEY_SUBSTRING_MIN))


def _redact_outbound(payload: Any, _path: str = "", _found: list[str] | None = None) -> Any:
    """把敏感字段的值换成显式标记,并在 stderr 说明动过哪几处。

    两条刻意的选择:

    ★ **换值,不是删键。** 删了键就等于说"平台没给这个字段",而那是假话;下游想知道
      "这台配没配监控账号"时会得到反的答案。

    ★ **None 原样保留。** "没配监控账号"和"配了但不给你看"是两件事,后者才需要遮。
      一律换成标记会把前者也说成后者。
    """
    top = _found is None
    if top:
        _found = []
        _announce_substring_layer_downgrade()
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            here = f"{_path}.{key}" if _path else key
            if _leaks(key, value, _path):
                _found.append(here)
                out[key] = _REDACTED
            else:
                out[key] = _redact_outbound(value, here, _found)
        payload = out
    elif isinstance(payload, list):
        payload = [_redact_outbound(v, f"{_path}[{i}]", _found)
                   for i, v in enumerate(payload)]
    if top and _found:
        # 悄悄改掉平台的答案比不改更糟 —— 读的人得知道这份输出被动过、动了哪几处。
        shown = sorted(set(_found))
        sys.stderr.write("[redacted] dba-skill 按自己的 Safety Rules 遮掉了 %d 处敏感字段:%s%s\n"
                         % (len(_found), "、".join(shown[:5]),
                            " 等" if len(shown) > 5 else ""))
    return payload


# 嵌套对象在表格里占一格,超过这个宽度就截断。csv/json 从不截断 —— 只有 table 会。
_CELL_MAX = 60


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

    # ★ 被截断的那些列必须自己说出来。table 是三种输出里**唯一会丢内容**的一种
    # (csv/json 都是完整的),而丢在哪儿只有渲染这一刻知道 —— 读的人手里只有一格看着
    # 像是完整值的文本。topology 是现成的例子:from/to 是十几个键的对象,一格根本装不下,
    # 于是表格看起来齐整、实际每行都缺东西。
    nested = [c for c in columns
              if any(isinstance(r.get(c), (dict, list))
                     and len(json.dumps(r.get(c), ensure_ascii=False)) > _CELL_MAX
                     for r in items)]
    if nested:
        # ★ 例子必须从**实际值**里取。第一版是往被截断的列名后面无脑拼 `.name`,
        # 结果 5 条会给提示的命令里有 4 条照抄就报 "no such field(s)" ——
        # `retire_exclusions` 是 list(点路径进不去)、`ai_review` 是 dict 但没有 name 键,
        # 只有 topology 的节点恰好有 name。**提示让人去敲一个敲不通的东西**,
        # 正是这一整轮在修的那个形状,只不过这次是我自己新种的。
        # 取不到能用的例子就不给例子:宁可少一句,不要给一条假的。
        examples = [e for e in (_dotted_example(items, c) for c in nested) if e]
        how = ("想在表里看具体某个字段,用点路径把它拉成一列,例如 --fields %s。"
               % ",".join(examples[:2])) if examples else \
              ("这几列是数组或只含嵌套对象,点路径展不开(--fields 只能钻 dict 的标量键),"
               "要完整内容就用 csv / json。")
        sys.stderr.write("[table] ★ %s 是嵌套对象,这一格放不下已被截断(行尾的 … 就是截断处)。"
                         "表格是唯一会截断的输出:--format csv / json 是完整的。%s\n"
                         % ("、".join(nested), how))
    return True


def _dotted_example(items: list[Any], column: str) -> str | None:
    """给出一条**在这份数据上真能用**的点路径,给不出就返回 None。

    判据就是 `_project` 用的那一条:`_dig` 能取到非 _MISSING 的标量。所以这里只认
    "dict 里的标量键" —— 数组进不去,嵌套 dict 再往下钻对读表的人也没帮助。
    """
    for row in items:
        value = row.get(column) if isinstance(row, dict) else None
        if not isinstance(value, dict):
            continue
        for key, sub in value.items():
            if sub is not None and not isinstance(sub, (dict, list)):
                return "%s.%s" % (column, key)
    return None


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        # ★ 截断要留痕。原来是直接 [:60],切完的 JSON 读起来**像一个完整的值**
        # (末尾正好落在 } 或引号上时尤其像),于是"这格还有内容没显示"和"这格就这么多"
        # 长得一模一样 —— 本项目的头号反模式,在最不起眼的一个函数里。
        return text if len(text) <= _CELL_MAX else text[:_CELL_MAX - 1] + "…"
    return str(value)


class _RateLimited(Exception):
    """HTTP 429. Carries how long the server asked us to wait, when it said."""

    def __init__(self, delay: float) -> None:
        super().__init__("rate limited")
        self.delay = delay


# The platform caps reads globally (600/minute) and live-database reads much lower. A wide
# fan-out therefore *will* be throttled — that is the design working, not a failure — so the
# client waits instead of handing the caller a hole. Capped low enough that a genuinely
# exhausted quota still fails loudly rather than hanging.
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BASE_DELAY = 1.0


def _retry_after_seconds(headers: Any) -> float:
    """Honour Retry-After when the server sends one; otherwise back off on our own."""
    raw = None
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:  # noqa: BLE001 - a header bag that will not be read is not fatal
        raw = None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return 0.0
    # A server asking for minutes is asking us to give up, not to sleep through the session.
    return value if 0 < value <= 60 else 0.0


def _request_once(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
    """One logical request, retried only for 429."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return _http_call(method, path, params=params, body=body)
        except _RateLimited as limited:
            if attempt == _RATE_LIMIT_RETRIES:
                _fail(
                    "rate_limited",
                    f"{method} {path} returned HTTP 429 after {attempt + 1} attempts",
                    status_code=429,
                    hint="Reads are capped at 600/minute globally and lower for live-database "
                         "probes. Narrow the fan-out or retry in a minute.",
                )
            delay = limited.delay or _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            sys.stderr.write(json.dumps({
                "warning": "rate_limited_retry",
                "message": f"{method} {path} was rate limited; waiting {delay:.1f}s "
                           f"(attempt {attempt + 1}/{_RATE_LIMIT_RETRIES}).",
            }, ensure_ascii=False) + "\n")
            time.sleep(delay)


def _http_call(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
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
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                # A 2xx that is not JSON almost always means the request never reached the
                # API: a base URL missing its /api/v2 prefix lands on the web server, which
                # answers 200 with an HTML page. "Expecting value: line 1 column 1" describes
                # the parser's disappointment, not the reader's problem — so say what came
                # back and where it came from.
                content_type = response.headers.get("Content-Type") or "unknown"
                _fail(
                    "invalid_json",
                    f"{method} {path} returned HTTP {response.status} with a non-JSON body "
                    f"(Content-Type: {content_type}). If this is HTML, PROJECT_API_BASE_URL is "
                    f"probably missing its /api/v2 suffix.",
                    url=url,
                    body_preview=_redact(raw.decode("utf-8", errors="replace")[:200]),
                    detail=str(exc),
                )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        global _LAST_HTTP_STATUS, _LAST_HTTP_BODY
        _LAST_HTTP_STATUS = exc.code
        _LAST_HTTP_BODY = raw
        if exc.code == 429:
            # Nothing ran: the limiter rejected the request before it reached the handler,
            # so this is safe to repeat. Retrying here rather than at the call site is the
            # point — every caller that forgets turns a transient limit into a stated fact,
            # which is how a fleet-wide onboarding sweep reported 17 cloud instances as
            # "no backup_method declared" when the platform had answered "not applicable"
            # for all of them and simply been asked too fast.
            raise _RateLimited(_retry_after_seconds(exc.headers)) from exc
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
    except (http.client.HTTPException, ConnectionError) as exc:
        # The request went out and the connection broke on the way back: getresponse() runs
        # outside urllib's URLError wrapping, and read() can end short (IncompleteRead). Left
        # uncaught it surfaced as a raw traceback instead of an error the caller can read.
        _fail("network_error", f"{method} {path} failed mid-response: {type(exc).__name__}: {exc}")
    except OSError as exc:
        # socket.timeout on Python 3.9 is an OSError but NOT a TimeoutError (3.10+ aliases it),
        # and a timeout while reading the status line escapes urllib's URLError wrapping — the
        # skill host runs 3.9, and `get /clusters` right after a deploy ended in a raw traceback.
        _fail("network_error", f"{method} {path} failed: {type(exc).__name__}: {exc}")
    except json.JSONDecodeError as exc:
        _fail("invalid_json", f"{method} {path} returned invalid JSON: {exc}")


def _common_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id")
    parser.add_argument("--instance-type")
    parser.add_argument("--instance-id", type=int)
    parser.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
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
    return _alerts.cmd_resolve(args, globals())


def cmd_context(args: argparse.Namespace) -> Any:
    return _alerts.cmd_context(args, globals())


def cmd_alert_evidence(args: argparse.Namespace) -> Any:
    return _alerts.cmd_alert_evidence(args, globals())


def cmd_alerts(args: argparse.Namespace) -> Any:
    return _alerts.cmd_alerts(args, globals())


def _short_reason(exc: BaseException) -> str:
    """Why a side call failed, short enough to sit inside another answer."""
    text = str(exc).strip() or exc.__class__.__name__
    return text[:160]


def cmd_whoami(args: argparse.Namespace) -> Any:
    return _core.cmd_whoami(args, globals())


def cmd_self_check(args: argparse.Namespace) -> Any:
    return _core.cmd_self_check(args, globals())


def cmd_backups_coverage(args: argparse.Namespace) -> Any:
    return _backups.cmd_backups_coverage(args, globals())


def cmd_capacity_forecast(args: argparse.Namespace) -> Any:
    return _cost.cmd_capacity_forecast(args, globals())


def cmd_silence_report(args: argparse.Namespace) -> Any:
    return _alerts.cmd_silence_report(args, globals())


def cmd_alerts_list(args: argparse.Namespace) -> Any:
    return _alerts.cmd_alerts_list(args, globals())


def cmd_classification(args: argparse.Namespace) -> Any:
    return _inventory.cmd_classification(args, globals())


def cmd_inventory_summary(args: argparse.Namespace) -> Any:
    return _inventory.cmd_inventory_summary(args, globals())


def cmd_databases_search(args: argparse.Namespace) -> Any:
    return _inventory.cmd_databases_search(args, globals())


def cmd_databases_unused(args: argparse.Namespace) -> Any:
    return _inventory.cmd_databases_unused(args, globals())


def cmd_ownership_scope(args: argparse.Namespace) -> Any:
    return _inventory.cmd_ownership_scope(args, globals())


def cmd_directory_options(args: argparse.Namespace) -> Any:
    return _inventory.cmd_directory_options(args, globals())


def cmd_freshness(args: argparse.Namespace) -> Any:
    return _alerts.cmd_freshness(args, globals())


def cmd_sweeps(args: argparse.Namespace) -> Any:
    return _inventory.cmd_sweeps(args, globals())


def cmd_timeline(args: argparse.Namespace) -> Any:
    return _alerts.cmd_timeline(args, globals())


def cmd_diagnostics_catalog(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_diagnostics_catalog(args, globals())


def cmd_diagnostics_run(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_diagnostics_run(args, globals())


def cmd_probe_catalog(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_probe_catalog(args, globals())


def cmd_ai_endpoints(args: argparse.Namespace) -> Any:
    return _core.cmd_ai_endpoints(args, globals())


def _try_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET that returns an error dict instead of exiting, for the composite commands.

    A composite answer must not vanish because one of its parts is unavailable — that is the
    difference between "this instance has no backup row" and "the whole question failed".
    """
    try:
        return _request_once("GET", path, params=params)
    except SystemExit:
        entry = {"path": path, "status_code": _LAST_HTTP_STATUS}
        _DEGRADED.append(entry)
        return {"unavailable": True, **entry}


def _unavailable(payload: Any) -> bool:
    """Did this part fail to load, as opposed to answering "nothing"?

    Every consumer of `_try_get` has to ask this, and the ones that forgot are the reason it
    exists: an unreadable dependency read as an empty answer, and an empty answer reads as a
    confirmed gap. Same shape, opposite meaning.
    """
    return isinstance(payload, dict) and payload.get("unavailable") is True


def _try_get_shared(path: str, params: dict[str, Any] | None = None) -> Any:
    """A fleet-wide read that is identical for every instance in a fan-out — fetched once.

    `onboarding-check` needs the whole-fleet backup and ELK coverage tables to decide whether
    a check even applies to this instance. Fetching them per instance made a 195-instance
    sweep issue 390 fleet-wide queries, which is what pushed it into the rate limit in the
    first place. Failures are not cached: one throttled call must not poison the rest of the
    run with a stale "unavailable".
    """
    key = (path, json.dumps(params or {}, sort_keys=True))
    cached = _SHARED_CACHE.get(key)
    if cached is not None and (time.monotonic() - cached[0]) < _SHARED_CACHE_TTL_SECONDS:
        return cached[1]
    payload = _try_get(path, params)
    if not _unavailable(payload):
        _SHARED_CACHE[key] = (time.monotonic(), payload)
    return payload


def _fail_if_instance_missing(instance_id: Any, detail: Any) -> None:
    """A 404 on the instance itself ends the composite answer — it does not degrade it.

    The composite commands degrade a part that fails to load, which is right for a missing
    backup row and wrong for a missing instance: `instance --instance-id 9999` exited 0 with
    database_count=0 and active_alerts=[] — "a box with no databases and no alerts" for a box
    that does not exist (2026-09-15 eval, MA22: 30 of ids 1–230 answered this way). Any other
    failure (403, 500, timeout) still degrades: the instance may well exist.
    """
    if _unavailable(detail) and detail.get("status_code") == 404:
        _fail("not_found", f"instance {instance_id} does not exist on this platform", exit_code=1,
              instance_id=instance_id)


def cmd_instance(args: argparse.Namespace) -> Any:
    return _inventory.cmd_instance(args, globals())


# What a newly onboarded instance is usually missing. Each is a separate subsystem, so
# "metrics look fine" says nothing about any of them.
_ONBOARDING_CHECKS = ("database_inventory", "backup_method", "ownership", "elk_logs")


def cmd_onboarding_check(args: argparse.Namespace) -> Any:
    return _inventory.cmd_onboarding_check(args, globals())


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


def _resolve_instance_id(args: argparse.Namespace) -> Any:
    """--instance-id,或从 --ip / --host 解析出来。找不到就响亮失败。"""
    if getattr(args, "instance_id", None) is not None:
        return args.instance_id
    ip, host = getattr(args, "ip", None), getattr(args, "host", None)
    if not (ip or host):
        return None
    resolved = _try_get("/dba/resolve", _clean_params({"ip": ip, "host": host}))
    for key in ("instances", "matches", "items"):
        rows = resolved.get(key) if isinstance(resolved, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0].get("instance_id") or rows[0].get("id")
    _fail("not_found", f"no instance matched ip={ip} host={host}", exit_code=1,
          resolve_response=resolved)


def cmd_topology(args: argparse.Namespace) -> Any:
    return _inventory.cmd_topology(args, globals())


def cmd_metric_series(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_metric_series(args, globals())


def cmd_get(args: argparse.Namespace) -> Any:
    return _core.cmd_get(args, globals())


def cmd_probe_run(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_probe_run(args, globals())


def cmd_business_inference_evidence(args: argparse.Namespace) -> Any:
    return _metadata.cmd_business_inference_evidence(args, globals())


def cmd_metadata_coverage(args: argparse.Namespace) -> Any:
    return _metadata.cmd_metadata_coverage(args, globals())


def cmd_database_objects(args: argparse.Namespace) -> Any:
    return _metadata.cmd_database_objects(args, globals())


def cmd_database_object_changes(args: argparse.Namespace) -> Any:
    return _metadata.cmd_database_object_changes(args, globals())


def cmd_search_database_objects(args: argparse.Namespace) -> Any:
    return _metadata.cmd_search_database_objects(args, globals())


def cmd_refresh_database_metadata(args: argparse.Namespace) -> Any:
    return _metadata.cmd_refresh_database_metadata(args, globals())


def cmd_metadata_refresh_status(args: argparse.Namespace) -> Any:
    return _metadata.cmd_metadata_refresh_status(args, globals())


def cmd_propose_metadata_refresh(args: argparse.Namespace) -> Any:
    return _metadata.cmd_propose_metadata_refresh(args, globals())


def cmd_propose_remote_backup_baseline_reset(args: argparse.Namespace) -> Any:
    return _metadata.cmd_propose_remote_backup_baseline_reset(args, globals())


def cmd_action_order_status(args: argparse.Namespace) -> Any:
    return _metadata.cmd_action_order_status(args, globals())


def cmd_execute_action_order(args: argparse.Namespace) -> Any:
    return _metadata.cmd_execute_action_order(args, globals())


def cmd_verify_action_order(args: argparse.Namespace) -> Any:
    return _metadata.cmd_verify_action_order(args, globals())


def cmd_prometheus_query(args: argparse.Namespace) -> Any:
    return _diagnostics.cmd_prometheus_query(args, globals())


def cmd_kb_search(args: argparse.Namespace) -> Any:
    return _knowledge.cmd_kb_search(args, globals())


def cmd_kb_incidents(args: argparse.Namespace) -> Any:
    return _knowledge.cmd_kb_incidents(args, globals())


def cmd_kb_doc_search(args: argparse.Namespace) -> Any:
    return _knowledge.cmd_kb_doc_search(args, globals())


def cmd_elk_status(args: argparse.Namespace) -> Any:
    return _elk.cmd_elk_status(args, globals())


def cmd_elk_coverage(args: argparse.Namespace) -> Any:
    return _elk.cmd_elk_coverage(args, globals())


def cmd_elk_search(args: argparse.Namespace) -> Any:
    return _elk.cmd_elk_search(args, globals())


def cmd_cloud_rightsizing(args: argparse.Namespace) -> Any:
    return _cost.cmd_cloud_rightsizing(args, globals())


def cmd_cloud_monitoring_coverage(args: argparse.Namespace) -> Any:
    return _cost.cmd_cloud_monitoring_coverage(args, globals())


def cmd_cloud_savings_realized(args: argparse.Namespace) -> Any:
    return _cost.cmd_cloud_savings_realized(args, globals())


def cmd_cloud_cost_history(args: argparse.Namespace) -> Any:
    return _cost.cmd_cloud_cost_history(args, globals())


def _year_on_year(years: Any, months: Any = None) -> Any:
    return _cost._year_on_year(years, months, runtime=globals())


def cmd_backups(args: argparse.Namespace) -> Any:
    return _backups.cmd_backups(args, globals())


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
             "and a partial page is shaped exactly like a complete one. Stops after "
             "--max-pages pages and says so on stderr.",
    )
    parser.add_argument(
        "--max-pages", type=_positive_int,
        default=(argparse.SUPPRESS if suppress_defaults else _MAX_PAGES),
        help=f"How many pages --all may follow (default {_MAX_PAGES}). Raise it when the "
             f"guard, not the data, is what ended the walk.",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        default=(argparse.SUPPRESS if suppress_defaults else False),
        help="Save this answer under ~/.dba-skill/snapshots so a later run can diff against it.",
    )
    parser.add_argument(
        "--since", metavar="WHEN",
        default=(argparse.SUPPRESS if suppress_defaults else None),
        help="Diff against the newest snapshot at or before WHEN: last, yesterday, today, "
             "6h, 3d, or an ISO timestamp. Fails loudly when there is nothing to compare "
             "with — an empty diff and 'no snapshot' must never look alike.",
    )
    parser.add_argument(
        "--only-if-changed", action="store_true",
        default=(argparse.SUPPRESS if suppress_defaults else False),
        help="With --since: print nothing and exit 0 when nothing changed. For scheduled runs "
             "— an hourly report that is identical every hour trains its reader to skip it. "
             "Still fails loudly when there is no baseline to compare with.",
    )
    parser.add_argument(
        "--group-by", metavar="FIELD",
        default=(argparse.SUPPRESS if suppress_defaults else None),
        help="Count rows per distinct value of FIELD (dotted paths allowed). Rows missing the "
             "field are counted under (missing), never dropped.",
    )
    parser.add_argument(
        "--sort-by", metavar="FIELD",
        default=(argparse.SUPPRESS if suppress_defaults else None),
        help="Order rows by FIELD (dotted paths allowed). Rows without it sort last.",
    )
    parser.add_argument(
        "--desc", action="store_true",
        default=(argparse.SUPPRESS if suppress_defaults else False),
        help="Sort descending. Only meaningful with --sort-by.",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        default=(argparse.SUPPRESS if suppress_defaults else False),
        help="Scalars only: every list at ANY depth becomes {count, omitted_by}. "
             "--count-only drops only TOP-LEVEL lists, so a response whose headline embeds "
             "its own lists still comes back large — cloud-rightsizing --count-only is still "
             "~10 KB because coupon_funded.instances and storage_summary.over_provisioned "
             "live inside dicts. This is the flag for 'just the numbers'.",
    )
    parser.add_argument(
        "--count-only", action="store_true",
        default=(argparse.SUPPRESS if suppress_defaults else False),
        help="Return the envelope's counts and drop the rows. Answers 'how many' without "
             "pulling a large collection through the context window.",
    )
    parser.add_argument(
        "--fields", default=default,
        help="Comma-separated fields to keep from each row (e.g. id,host,type,status). "
             "A full inventory dump is hundreds of KB; four columns is usually the answer.",
    )
    parser.add_argument(
        "--format", choices=["json", "table", "csv"],
        default=(argparse.SUPPRESS if suppress_defaults else "json"),
    )


# 知识库按引擎过滤时的取值表。原本是 build_parser 里的局部量,夹在两个命令块之间 ——
# 命令定义一旦按域拆开,跨块共享的局部量就会变成 NameError(2026-09-21 拆分时当场撞到)。
_KB_DB_TYPES = ["oracle", "mysql", "postgres", "tidb", "clickhouse"]


def _add_core_commands(sub) -> None:
    return _core._add_core_commands(sub, globals())


def _add_alert_commands(sub) -> None:
    return _alerts._add_alert_commands(sub, globals())


def _add_backup_commands(sub) -> None:
    return _backups._add_backup_commands(sub, globals())


def _add_inventory_commands(sub) -> None:
    return _inventory._add_inventory_commands(sub, globals())


def _add_diagnostic_commands(sub) -> None:
    return _diagnostics._add_diagnostic_commands(sub, globals())


def _add_capacity_commands(sub) -> None:
    return _cost._add_capacity_commands(sub, globals())


def _add_knowledge_commands(sub) -> None:
    return _knowledge._add_knowledge_commands(sub, globals())


def _add_elk_commands(sub) -> None:
    return _elk._add_elk_commands(sub, globals())


def _add_cost_commands(sub) -> None:
    return _cost._add_cost_commands(sub, globals())


def build_parser() -> argparse.ArgumentParser:
    """命令定义按域分在 _add_*_commands 里。

    这个函数原本 443 行,是全文件唯一**随时间必然变长**的地方(每加一个命令就长一点)。
    ★ 等价性不是靠读代码保证的:重构前后 41 份 `--help` 输出做过逐字比对。
    ★ 拆分时当场撞到一个坑:命令块之间夹着共享的局部量(_KB_DB_TYPES),按域拆开后它变成
      NameError —— 已提为模块级常量。再有这类共享量,也要先提上去再拆。
    """
    parser = argparse.ArgumentParser(description="Call Database AI Center DBA APIs safely.")
    _add_global_output_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    _add_core_commands(sub)
    _add_alert_commands(sub)
    _add_backup_commands(sub)
    _add_inventory_commands(sub)
    _add_diagnostic_commands(sub)
    _add_capacity_commands(sub)
    _add_knowledge_commands(sub)
    _add_elk_commands(sub)
    _add_cost_commands(sub)

    # Accept the flags after the subcommand too — `inventory-summary --all` is what anyone
    # types first, and argparse would otherwise only honour them before the subcommand.
    for action in sub.choices.values():
        _add_global_output_flags(action, suppress_defaults=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    global _FETCH_ALL, _MAX_PAGES, _COUNT_ONLY, _SUMMARY_ONLY
    _FETCH_ALL = bool(getattr(args, "all", False))
    _MAX_PAGES = int(getattr(args, "max_pages", None) or _MAX_PAGES)
    _COUNT_ONLY = bool(getattr(args, "count_only", False))
    _SUMMARY_ONLY = bool(getattr(args, "summary_only", False))
    ids_raw = getattr(args, "instance_ids", None)
    if getattr(args, "_needs_instance", False) and not ids_raw and getattr(args, "instance_id", None) is None:
        # argparse cannot express "exactly one of these two", and making --instance-id required
        # would reject the batch form outright. Checked here so the message names both.
        _fail("missing_instance", "This command needs --instance-id N, or --instance-ids "
                                  "N,N,N to run it for several at once.", exit_code=2)
    if ids_raw and getattr(args, "instance_id", None) is not None:
        _fail("conflicting_instance", "Pass --instance-id or --instance-ids, not both.",
              exit_code=2)
    if ids_raw:
        payload = _fan_out(args, _parse_instance_ids(ids_raw))
    else:
        payload = args.func(args)
    # ★ 脱敏放在**取回后立刻**,不是渲染时:--snapshot 会把 payload 原样写到磁盘,
    #   放在渲染那一步等于快照文件里还留着。这里是所有命令唯一的收口处。
    payload = _redact_outbound(payload)
    # 回显的是**生效的查询**,包含没被显式传入的默认值 —— 默认值恰恰是最该说出来的那部分:
    # 问"有告警吗"拿到 0,你得知道它只看了 status=active、且封顶 limit=200。
    # 不覆盖服务端自己给的 `filters`(databases-search 已经有了),那份是权威。
    _filters = _applied_filters(args)
    if _filters and isinstance(payload, dict) and "filters" not in payload:
        payload = {**payload, "applied_filters": _filters}

    fresh = payload  # what the server just said, before any diffing rewrites `payload`

    since_raw = getattr(args, "since", None)
    want_snapshot = bool(getattr(args, "snapshot", False))
    platform_version = _platform_version() if (since_raw or want_snapshot) else None

    if getattr(args, "only_if_changed", False) and not since_raw:
        _fail("missing_since", "--only-if-changed needs --since: without a baseline there is "
                               "nothing to be quiet about.", exit_code=2)
    if since_raw:
        since = _parse_since(since_raw)
        if since is None:
            _fail("invalid_since", f"--since {since_raw!r} not understood. Use last, yesterday, "
                                   f"today, 6h, 3d, or an ISO timestamp.", exit_code=2)
        previous = _load_snapshot(sys.argv[1:], since)
        if previous is None:
            # The whole point of a diff is telling someone what moved. Returning an empty diff
            # here would say "nothing changed" when the truth is "there is nothing to compare
            # with" — the exact confusion this feature exists to remove.
            _fail(
                "no_snapshot",
                f"No snapshot for this command at or before {since_raw}. Take one first: "
                f"re-run with --snapshot, then come back later with --since.",
                exit_code=2,
                snapshot_dir=str(_snapshot_dir() or "<home unavailable>"),
            )
        diff = _diff_payloads(previous.get("payload"), payload)
        diff = _annotate_judgement_changes(
            diff, previous.get("platform_version"), platform_version
        )
        payload = {
            "compared_with": previous.get("path"),
            "baseline_taken_at": previous.get("taken_at"),
            "current_at": datetime.now(timezone.utc).isoformat(),
            **diff,
        }
        if getattr(args, "only_if_changed", False) and not (
            diff["added"] or diff["removed"] or diff["changed"]
        ):
            # Silence is the message. Note the baseline is still refreshed below when
            # --snapshot was asked for, so tomorrow compares against today, not last week.
            if want_snapshot:
                _write_snapshot(fresh, sys.argv[1:], platform_version)
            return 0

    if want_snapshot:
        # Snapshot what the server said, not the diff — and never by calling the endpoint a
        # second time: two calls a moment apart are two different answers, and the snapshot
        # would then describe neither the diff's baseline nor its result.
        saved = _write_snapshot(fresh, sys.argv[1:], platform_version)
        if saved:
            sys.stderr.write(json.dumps({"snapshot": saved}, ensure_ascii=False) + "\n")

    if getattr(args, "summary_only", False):
        payload = _summary_only(payload)
    elif _COUNT_ONLY:
        payload = _counts_only(payload)
    payload = _sort_rows(payload, getattr(args, "sort_by", None), bool(getattr(args, "desc", False)))
    payload = _project(payload, _split_fields(getattr(args, "fields", None)))
    group_by = getattr(args, "group_by", None)
    if group_by:
        # After --fields, so a projection can narrow what is grouped; the grouped result is
        # counts, so table/csv render it the same as any other collection.
        payload = _group_rows(payload, group_by)
    fmt = getattr(args, "format", "json")
    if fmt == "csv":
        rendered = _to_csv(payload)
        if rendered is None:
            lists = _lists_present(payload)
            # exit_code 与 table/sort-by/group-by 一致:同一类拒绝给出不同退出码,
            # 是给调用方脚本埋的小陷阱。
            _fail("not_tabular", "--format csv needs a collection; this response is a single "
                  "object" + (" (it carries list(s) under %s, but none is the response's "
                              "collection)" % ", ".join(lists) if lists else ""), exit_code=2)
        sys.stdout.write(rendered)
        return 0
    if fmt == "table":
        if _print_table(payload):
            return 0
        # ★ 此前这里直接掉进 _print_json:exit 0、吐原始 JSON、stderr 空 —— 旗标什么都没做
        # 也什么都没说。--format csv 早就是响的,table 却不是;三个收窄旗标里唯一静默失败的
        # 那个,把"失败是响的"这条纪律在最后一步断掉了。
        lists = _lists_present(payload)
        detail = ("this response is a single object; it does carry list(s) under %s, but "
                  "none of them is the response's collection, so there is no single table to "
                  "render — pass --fields to project one, or read it as JSON."
                  % ", ".join(lists)) if lists else \
                 "this response is a single object with no collection to tabulate."
        _fail("not_tabular", "--format table needs a collection; " + detail, exit_code=2)
    _print_json(payload)
    return 0


def _run() -> int:
    """Run main, treating a closed pipe as the ordinary end of output.

    `| head` closes the pipe mid-write; Python then raises again while flushing stdout at
    interpreter shutdown and prints a traceback. The rows already written were correct and
    the truncation was the caller's own choice, so a traceback here is pure noise that reads
    like the command failed — and `| head` is the most common way to look at these outputs.

    stdout is redirected to devnull first so the shutdown flush has somewhere to go; without
    that, the same error is raised again after this handler returns.

    The flush has to happen HERE, not at interpreter shutdown. A table of a few hundred rows
    fits entirely in stdout's buffer, so every write succeeds, main() returns 0, and nothing
    is raised inside this try at all — the pipe is only touched when the interpreter drains
    the buffer on the way out, long after this handler is gone. That is why the first version
    of this fix looked right, tested green on small outputs, and still printed the traceback
    for `--all ... | head`: it was guarding a moment the error never happened in.
    """
    try:
        rc = main()
        sys.stdout.flush()
        return rc
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(_run())
