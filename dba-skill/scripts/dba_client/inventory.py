"""Inventory command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_classification(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/instances/classification",
        params={
            "type": args.type,
            "topology": args.topology,
            "tenant_id": args.tenant_id,
            "limit": getattr(args, "limit", None),
        },
    )


def cmd_inventory_summary(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    params = runtime["_common_filter_params"](args)
    params.update(
        {
            "include_system_dbs": args.include_system_dbs,
            "stale_after_hours": args.stale_after_hours,
        }
    )
    return runtime["_request"]("GET", "/dba/inventory/summary", params=params)


def cmd_databases_search(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    params = runtime["_common_filter_params"](args)
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
    return runtime["_request"]("GET", "/dba/databases/search", params=params)


def cmd_databases_unused(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    params = runtime["_common_filter_params"](args)
    params.update({"include_inactive": args.include_inactive, "limit": args.limit})
    return runtime["_request"]("GET", "/dba/databases/unused", params=params)


def cmd_ownership_scope(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
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


def cmd_directory_options(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/dba/directory/options",
        params={
            "type": args.type,
            "search": args.search,
            "include_inactive": args.include_inactive,
            "limit": args.limit,
        },
    )


def cmd_sweeps(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Nightly database-discovery sweep runs (platform 3.83.0+).

    Answers "why was this instance not discovered last night?" — a question that previously had
    no answer anywhere: the sweep computed the skip reasons and threw them into a log line.
    Each row carries the skipped distribution, which is the whole point of reading this.
    """
    return runtime["_request"]("GET", "/dba/database-discovery/sweeps", params={"limit": args.limit})


def cmd_instance(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Everything about one instance, from an id or an IP, in one call.

    "Here is an IP, tell me about this box" is the most frequent question and it used to take
    several calls across two path prefixes. Each part is fetched independently and a missing
    part is reported as `unavailable` rather than collapsing the whole answer.
    """
    instance_id = args.instance_id
    resolved: Any = None
    if instance_id is None:
        if not (args.ip or args.host):
            runtime["_fail"]("invalid_argument", "instance requires --instance-id, --ip or --host", exit_code=2)
        resolved = runtime["_try_get"]("/dba/resolve", runtime["_clean_params"]({"ip": args.ip, "host": args.host}))
        for key in ("instances", "matches", "items"):
            rows = resolved.get(key) if isinstance(resolved, dict) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                instance_id = rows[0].get("instance_id") or rows[0].get("id")
                break
        if instance_id is None:
            runtime["_fail"]("not_found", f"no instance matched ip={args.ip} host={args.host}",
                  exit_code=1, resolve_response=resolved)

    detail = runtime["_try_get"](f"/instances/{instance_id}")
    runtime["_fail_if_instance_missing"](instance_id, detail)
    databases = runtime["_try_get"]("/databases", {"instance_id": instance_id, "limit": 1})
    alerts = runtime["_try_get"]("/alerts", {"instance_id": instance_id, "status": "active", "page_size": 50})
    alert_items, _ = runtime["_envelope"](alerts)
    db_items, db_meta = runtime["_envelope"](databases)
    return {
        "instance_id": instance_id,
        "instance": detail,
        "freshness": runtime["_try_get"](f"/dba/instances/{instance_id}/freshness"),
        "backups": runtime["_try_get"](f"/instances/{instance_id}/backups"),
        "database_count": db_meta.get("total") if db_meta else (len(db_items) if db_items else None),
        "database_inventory_coverage": (detail or {}).get("database_inventory_coverage")
        if isinstance(detail, dict) else None,
        "active_alerts": alert_items or [],
        "resolved_from": {"ip": args.ip, "host": args.host} if resolved is not None else None,
    }


def cmd_onboarding_check(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Is this newly onboarded instance actually wired up?

    Metrics start flowing immediately, which is exactly what makes the rest easy to miss:
    databases undiscovered, backup method undeclared, no owner, not shipping logs. Four
    subsystems, four separate answers — this asks all four and says which are missing.

    Each check has four possible outcomes, not two: covered, a real gap, not this instance's
    job, or unanswerable right now. Collapsing the last two into "missing" is what turned a
    throttled fleet sweep into 17 fabricated backup gaps on instances the platform had
    already declared not applicable.
    """
    iid = args.instance_id
    detail = runtime["_try_get"](f"/instances/{iid}")
    runtime["_fail_if_instance_missing"](iid, detail)
    databases = runtime["_try_get"]("/databases", {"instance_id": iid, "limit": 1})
    backups = runtime["_try_get"](f"/instances/{iid}/backups")
    # Fleet-wide tables, shared across a fan-out rather than refetched per instance.
    elk = runtime["_try_get_shared"]("/elk/coverage")
    coverage_payload = runtime["_try_get_shared"]("/dba/backups/coverage", {"limit": 2000})

    detail_ok = isinstance(detail, dict) and not runtime["_unavailable"](detail)
    db_items, db_meta = runtime["_envelope"](databases)
    db_count = db_meta.get("total") if db_meta else (len(db_items) if db_items else 0)
    coverage = detail.get("database_inventory_coverage") if detail_ok else None
    method = (backups or {}).get("backup_method") if isinstance(backups, dict) else None
    contact = detail.get("contact_person") if detail_ok else None
    host = detail.get("host") if detail_ok else None
    cluster_id = detail.get("cluster_id") if detail_ok else None

    # /elk/coverage is a fourth shape again: the rows live under `instances`, each carrying
    # its own `covered` flag. Matching on `items` + host returned an empty set and reported
    # every instance as "not shipping logs" — a wrong-field lookup and a genuine gap produce
    # the same empty answer, which is the whole reason this command reports `unavailable`
    # separately from `ok: false`.
    elk_rows = elk.get("instances") if isinstance(elk, dict) else None
    elk_readable = isinstance(elk_rows, list)
    elk_row = next(
        (r for r in (elk_rows or [])
         if isinstance(r, dict) and str(r.get("id")) == str(iid)), None
    )

    # Whether these subsystems are this instance's job at all — read from the platform, not
    # re-derived here. Cloud RDS has no host to ship logs from and its backups belong to the
    # vendor, so "no logs" and "no backup_method" are correct states, not gaps. Reporting them
    # as missing would hang a permanent false gap on all 115 cloud instances, and a checklist
    # that is never green is one people stop reading. The platform already answers both
    # (`applicable` on the ELK row, `not_applicable` as the backup verdict); asking it keeps
    # one definition of the rule instead of a copy here that drifts.
    coverage_rows, _ = runtime["_envelope"](coverage_payload)
    coverage_readable = not runtime["_unavailable"](coverage_payload) and coverage_rows is not None
    backup_row = next(
        (r for r in (coverage_rows or [])
         if isinstance(r, dict) and str(r.get("instance_id")) == str(iid)), None
    )
    backup_na = (backup_row or {}).get("verdict") == "not_applicable"
    backup_na_reason = "; ".join((backup_row or {}).get("reasons") or []) or None
    elk_na = elk_row is not None and elk_row.get("applicable") is False
    elk_na_reason = (elk_row or {}).get("not_applicable_reason")
    # A cluster component's owner is held by the cluster head — the same shape as its backup,
    # which the platform already calls not applicable. Demanding one per component turned a
    # single unowned TiDB cluster into 21 identical "no owner" rows, burying the one row that
    # someone could actually act on.
    component = coverage == "cluster_component"

    # An instance that is not in service is not being onboarded. The platform's own coverage
    # tables list active instances only, so judging a retired one against them produced three
    # "gaps" on a decommissioned host — an item nobody can close, on a checklist people are
    # meant to work through to zero.
    status = str(detail.get("status") or "").strip().lower() if detail_ok else ""
    if status and status != "active":
        reason = f"instance status={status}: not in service, nothing to onboard"
        checks = {name: {"ok": None, "not_applicable": reason}
                  for name in ("database_inventory", "backup_method", "ownership", "elk_logs")}
        return {
            "instance_id": iid, "checks": checks, "missing": [],
            "not_applicable": list(checks), "unknown": [], "unavailable": [],
            "status": status, "ok": True,
        }

    findings: dict[str, dict[str, Any]] = {}

    # An empty list on a cluster member is correct: the owner holds the rows.
    if runtime["_unavailable"](databases) or not detail_ok:
        findings["database_inventory"] = {
            "ok": None, "unavailable": "could not read the instance or its databases"}
    else:
        findings["database_inventory"] = {
            "ok": bool(db_count) or coverage in ("cluster_covered", "cluster_component"),
            "databases": db_count, "coverage": coverage,
        }

    if not coverage_readable:
        # Without the coverage table there is no way to know whether this instance's backups
        # are even ours to declare, so an undeclared method proves nothing.
        findings["backup_method"] = {
            "ok": None, "unavailable": "could not read /dba/backups/coverage"}
    elif backup_na:
        findings["backup_method"] = {
            "ok": None, "not_applicable": backup_na_reason or "backups are the provider's"}
    elif backup_row is None:
        # Readable table, no row for this instance — that is not evidence of anything.
        findings["backup_method"] = {
            "ok": None,
            "unavailable": "no row in /dba/backups/coverage, so whether backups are this "
                           "instance's responsibility is unknown"}
    elif runtime["_unavailable"](backups):
        findings["backup_method"] = {
            "ok": None, "unavailable": f"could not read /instances/{iid}/backups"}
    else:
        findings["backup_method"] = {
            "ok": bool(method), "backup_method": method,
            "why": "undeclared makes 'no backup' and 'expdp not declared' indistinguishable"}

    if not detail_ok:
        findings["ownership"] = {"ok": None, "unavailable": f"could not read /instances/{iid}"}
    elif component:
        findings["ownership"] = {
            "ok": None, "cluster_id": cluster_id,
            "not_applicable": (
                f"cluster component: the owner is held by cluster head {cluster_id}"
                if cluster_id is not None else
                "cluster component: the owner is held by the cluster head"),
        }
    else:
        findings["ownership"] = {"ok": bool(contact), "contact_person": contact}

    # Three states, not two: covered, a real gap, or not this instance's job. Unreadable
    # coverage is a fourth — say which one it is rather than folding them together.
    findings["elk_logs"] = (
        {"ok": None, "host": host,
         "not_applicable": elk_na_reason or "logs are not shipped for this instance kind"}
        if elk_na else
        {"ok": bool(elk_row and elk_row.get("covered")), "host": host}
        if elk_readable
        else {"ok": None, "host": host, "unavailable": "could not read /elk/coverage"}
    )

    missing = [name for name, f in findings.items() if f["ok"] is False]
    not_applicable = [name for name, f in findings.items() if f.get("not_applicable")]
    unavailable = [name for name, f in findings.items() if f.get("unavailable")]
    unknown = [name for name, f in findings.items()
               if f["ok"] is None and name not in not_applicable]
    return {
        "instance_id": iid,
        "checks": findings,
        "missing": missing,
        "not_applicable": not_applicable,
        # A check the platform could not answer is reported apart from one it answered "no":
        # collapsing them would let an outage read as a clean bill of health.
        "unknown": unknown,
        "unavailable": unavailable,
        "ok": not missing and not unknown,
    }


def cmd_topology(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """谁复制给谁 —— 含**未纳管的外部主机**,而这正是 `/clusters` 给不出的那部分。

    "这台的主库/备库是谁"以前被文档指向 `/clusters`。那是另一个问题的答案:集群成员关系
    只能包含**纳管的**实例。生产 2026-09-08 实测:37 条复制边里 **33 条(89%)**有一端是
    `ext:` 开头的外部主机,229 个节点里 33 个是外部的。所以 `/clusters` 最多能看见 4 条 ——
    **而它给出的答案看起来是完整的**,这比给不出更糟。

    边只带 id,不带名字。不 inline 的话,每个调用方都得自己拿 nodes 做一次连接,而那一步
    做错了没有任何东西会报错 —— 和 `alerts` 把 instance 名字 inline 进来是同一个理由。
    """
    payload = runtime["_request"]("GET", "/topology")
    if not isinstance(payload, dict):
        return payload
    nodes = {n.get("id"): n for n in (payload.get("nodes") or []) if isinstance(n, dict)}
    edges = [e for e in (payload.get("edges") or []) if isinstance(e, dict)]

    def side(ref: Any) -> dict[str, Any]:
        if isinstance(ref, str) and ref.startswith("ext:"):
            # 未纳管:平台只知道它的地址,别的一无所知。说清楚,不要留一个裸 id。
            return {"ref": ref, "external": True, "endpoint": ref[4:], "name": None,
                    "note": "未纳管主机——平台只知道地址,没有它的指标/备份/负责人"}
        node = nodes.get(ref) or {}
        # ★ **透传,不是白名单。** 第一版挑了 5/12 个键,丢掉的里面有 role_detail(143 个
        #   instance_role="primary" 被它拆成 primary 44 / source 98 / mgr_primary 1 ——
        #   MGR 主库和普通异步主库的运维动作不一样)和 is_rac(227 个节点里 14 个为真)。
        #   第二版把名单补齐到 12 个,**但那仍然是白名单**:平台哪天加第 13 个键,
        #   它照样被丢,而且用例里的 `set(node)` 取自测试夹具、不是平台产出,所以**测试照样绿**。
        #   白名单的问题测试补不上,只能靠结构:默认全带过来,只显式改写我自己算出来的那两个。
        #   丢掉 id 是因为 ref 就是它(边上引用的就是这个值),留着只会让人以为是两个东西。
        out = {"ref": ref, "external": bool(node.get("external"))}
        out.update({k: v for k, v in node.items() if k not in ("id", "ref", "external")})
        return out

    focus = runtime["_resolve_instance_id"](args)
    rows = []
    for e in edges:
        # 同样是透传:边上除了 from/to(要换成解析后的对象)以外的字段一律原样带过去。
        # `resolved_by`(平台标"这条边是靠地址猜的")第一版就是这么丢掉的 —— 平台标 2 条、
        # 我这儿显示 0 条;那个标记存在的全部理由就是让人看得见。补进白名单只能挡住这一个,
        # 透传才能挡住下一个。
        row = {"kind": e.get("kind"), "sync_state": e.get("sync_state")}
        row.update({k: v for k, v in e.items()
                    if k not in ("from", "to", "kind", "sync_state")})
        row["from"] = side(e.get("from"))
        row["to"] = side(e.get("to"))
        if focus is not None and focus not in (e.get("from"), e.get("to")):
            continue
        if args.external_only and not (row["from"]["external"] or row["to"]["external"]):
            continue
        rows.append(row)

    ext_edges = sum(1 for e in edges
                    if str(e.get("from")).startswith("ext:") or str(e.get("to")).startswith("ext:"))
    out: dict[str, Any] = {
        # 键名用 items 而不是 edges:_envelope 只认那几个集合键,叫别的名字会让
        # --fields / --group-by / --sort-by / --count-only / --format table **全部静默失效**。
        # 描述性更强的名字换来的是一堆不工作的旗标 —— 不划算。
        "items": rows,
        "item_kind": "replication_edge",
        "focus_instance_id": focus,
        "coverage": {
            "edges_total": len(edges),
            "edges_touching_unmanaged": ext_edges,
            "nodes_total": len(nodes),
            "nodes_unmanaged": sum(1 for n in nodes.values() if n.get("external")),
            "note": (
                "复制关系里带 ext: 的一端是**未纳管**主机。/clusters 结构上只能显示纳管成员,"
                "所以复制链上有外部主机时,用它回答「主备是谁」会给出一个**残缺但看起来完整**的答案 —— "
                "这里的 edges_touching_unmanaged 就是它看不见的部分。反过来的情况也有:MGR 组复制"
                "在平台 3.80 之前不画边,MGR 实例带 cluster_id 却没有边时,成员与角色看 /clusters/{id}/members。"
            ),
        },
    }
    if focus is not None and not rows:
        # 空结果要说清是哪一种空。
        focus_node = nodes.get(focus) or {}
        cluster_id = focus_node.get("cluster_id")
        if cluster_id is not None:
            # 它在集群里:成员与角色在集群那边,别把人引到"查不到主备"。但没有边的**原因**因集群
            # 类型而异,套错了会让 agent 把原因说错(复查:MGR 那句一度被套到 RAC、DG 备库上)。
            detail = str(focus_node.get("role_detail") or "").lower()
            coarse = str(focus_node.get("instance_role") or "").lower()
            # 两个字段都看:DG 备库常是 instance_role=physical_standby、role_detail=active_dg ——
            # 只看 role_detail 会把它漏进通用分支(复查时生产上的一台 DG 备库就是这样)。
            is_mirror = (not detail.startswith("mgr_")) and (
                any(t in coarse for t in ("standby", "replica"))
                or any(t in detail for t in ("standby", "replica", "dg"))
            )
            if is_mirror:
                why = "它和主库之间的复制关系没有上报到拓扑(拓扑只画各实例自己上报的主从信息)。"
            elif detail.startswith("mgr_"):
                why = "MGR 组复制在平台 3.80 之前不画边。"
            elif focus_node.get("is_rac"):
                why = "RAC 节点共享同一个库,节点之间本来就没有复制边。"
            else:
                why = "它的主从关系没有上报到拓扑(拓扑只画各实例自己上报的主从信息)。"
            out["note"] = (
                "实例 %s 在拓扑里没有复制边,但它属于集群 %s:%s成员与角色见 get /clusters/%s/members;"
                "复制链上若还有未纳管的主机,那里也看不到。" % (focus, cluster_id, why, cluster_id)
            )
            out["cluster_id"] = cluster_id
        else:
            out["note"] = (
                "实例 %s 在拓扑里没有任何复制边。★ 这有两种含义,而它们长得一样:"
                "「它确实是单机」,或者「复制关系没被发现」——拓扑是从各实例自己上报的主从信息推的,"
                "一端不上报就整条边都看不到。**不要据此断言它没有备库。**" % focus
            )
    return out




def _add_inventory_commands(sub, runtime: dict[str, Any]) -> None:
    """库存、负责人与库发现。"""
    inventory = sub.add_parser("inventory-summary")
    runtime["_common_filters"](inventory)
    inventory.add_argument("--include-system-dbs", action="store_true")
    inventory.add_argument("--stale-after-hours", type=int)
    inventory.set_defaults(func=runtime["cmd_inventory_summary"])

    search = sub.add_parser("databases-search")
    runtime["_common_filters"](search)
    search.add_argument("--status")
    search.add_argument("--include-inactive", action="store_true")
    search.add_argument("--include-system-dbs", action="store_true")
    search.add_argument("--is-in-use", choices=["true", "false"])
    search.add_argument("--limit", type=int)
    search.add_argument("--offset", type=int)
    search.set_defaults(func=runtime["cmd_databases_search"])

    unused = sub.add_parser("databases-unused")
    runtime["_common_filters"](unused)
    unused.add_argument("--include-inactive", action="store_true")
    unused.add_argument("--limit", type=int)
    unused.set_defaults(func=runtime["cmd_databases_unused"])

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
    scope.set_defaults(func=runtime["cmd_ownership_scope"])

    directory = sub.add_parser("directory-options")
    directory.add_argument("--type", choices=["contact", "department", "application"], required=True)
    directory.add_argument("--search")
    directory.add_argument("--include-inactive", action="store_true")
    directory.add_argument("--limit", type=int)
    directory.set_defaults(func=runtime["cmd_directory_options"])

    freshness = sub.add_parser("freshness")
    freshness.add_argument("--instance-id", type=int)
    freshness.set_defaults(_needs_instance=True)
    freshness.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    freshness.add_argument("--stale-after-hours", type=int)
    freshness.set_defaults(func=runtime["cmd_freshness"])

    sweeps = sub.add_parser(
        "sweeps",
        help="Nightly database-discovery sweep runs: what each one attempted, deferred and skipped (3.83.0+).",
    )
    sweeps.add_argument("--limit", type=int, help="Runs to return, newest first (server max 100).")
    sweeps.set_defaults(func=runtime["cmd_sweeps"])

    timeline = sub.add_parser("timeline")
    timeline.add_argument("--instance-id", type=int)
    timeline.set_defaults(_needs_instance=True)
    timeline.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    timeline.add_argument("--hours", type=int)
    timeline.add_argument("--limit", type=int)
    timeline.set_defaults(func=runtime["cmd_timeline"])
