"""Diagnostics command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_diagnostics_catalog(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", f"/dba/instances/{args.instance_id}/diagnostics/catalog")


def cmd_diagnostics_run(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    if args.sql:
        runtime["_fail"](
            "free_form_sql_not_supported",
            "diagnostics-run only accepts catalog check ids; free-form SQL is not supported",
            exit_code=2,
        )
    body: dict[str, Any] = {"checks": runtime["_checks"](args.checks)}
    if args.timeout_seconds is not None:
        body["timeout_seconds"] = args.timeout_seconds
    if args.database_name:
        body["database_name"] = args.database_name
    return runtime["_request"]("POST", f"/dba/instances/{args.instance_id}/diagnostics/run", body=body)


def cmd_probe_catalog(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", f"/instances/{args.instance_id}/diagnostics/catalog")


def cmd_metric_series(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """一个指标的走势。`latest` 只给一个点,而"它一直这样还是刚变的"要靠曲线回答。

    ★ 24 小时是一道**硬边界**:窗口一旦超过它就改从汇总表取数,而**云采集的指标从不写汇总表**
    (见平台 v3.64.x)。所以对云 RDS 实例问 48 小时的 `threads_running`,平台会返回 422 而不是
    一个空数组 —— 那个 422 是答案的一部分,不是调用失败。这里原样透出它,并把粒度切换点讲清楚。
    """
    instance_id = runtime["_resolve_instance_id"](args)
    if instance_id is None:
        runtime["_fail"]("missing_instance", "metric-series needs --instance-id N, or --ip/--host.",
              exit_code=2)
    payload = runtime["_try_get"](
        f"/metrics/{instance_id}/series",
        runtime["_clean_params"]({"metric_name": args.metric_name, "hours": args.hours,
                       "granularity": args.granularity}),
    )
    if runtime["_unavailable"](payload):
        if runtime["_LAST_HTTP_STATUS"] != 422:
            runtime["_fail"]("http_error",
                  "GET /metrics/%s/series returned HTTP %s" % (instance_id, runtime["_LAST_HTTP_STATUS"]),
                  status_code=runtime["_LAST_HTTP_STATUS"])
        # ★ 这里的 422 **是答案的一部分**,不是调用失败:平台在说「这个指标没有该粒度的汇总,
        # 所以这个窗口什么都给不出」——而那正是提问者需要知道的。塞进 http_error 里等于把
        # 答案降级成报错,读的人会以为是自己调错了。
        try:
            body = runtime["json"].loads(runtime["_LAST_HTTP_BODY"] or "{}")
        except ValueError:
            body = {}
        reason = body.get("message") or body.get("detail") or (runtime["_LAST_HTTP_BODY"] or "")[:400]
        out = {
            "instance_id": instance_id,
            # ★ 集合键必须和成功路径**同名**。上一轮把成功路径从 points 改成 items,
            # 这条 422 路径没跟着改,于是同一条命令返回两种形状:调用方写 out["items"],
            # 正常时拿到数据,一遇到 422 就 KeyError 或静默拿不到。
            # SKILL.md 自己写着「集合可能落在 items / rows / ... 猜错就是运行时崩溃」——
            # 结果同一条命令自己就给了两种。item_kind 也一并给出,让空集合仍能自证是什么的空集合。
            "items": [],
            "item_kind": "metric_point",
            "unavailable": True,
            "reason": reason,
        }
        # ★ 只在平台**确实**在说"没有汇总"时才给那条建议。
        # 这个端点的 422 不止一种来源:hours 超过 720 是参数校验,granularity=raw 配大窗口
        # 是另一条拒绝——对它们说"用 --hours 24 拿原始点"是**错的建议**,而错的建议比没有建议
        # 更贵。第一版就是无条件加,等于我自己种下了今天一直在修的那类问题:回答了,但把人带偏。
        # 匹配不上时**不加提示**:平台那两条消息本身已经自解释,让它原样过去。
        if "aggregates" in reason:
            out["hint"] = (
                "窗口超过 24 小时会切到汇总表,而云采集的指标从不写汇总表(平台 v3.64.x)。"
                "用 --hours 24 拿原始点,或换一个有汇总的指标。"
                "★ 这不是调用失败,是这个问题在这个窗口上没有答案。"
            )
        return out
    if not isinstance(payload, list):
        return payload
    granularities = sorted({r.get("granularity") for r in payload if isinstance(r, dict)})
    names = sorted({r.get("metric_name") for r in payload if isinstance(r, dict)})
    values = [r.get("value") for r in payload
              if isinstance(r, dict) and isinstance(r.get("value"), (int, float))]
    out: dict[str, Any] = {
        "instance_id": instance_id,
        # ★ 键名用 items。第一版叫 `points`(更贴切),结果 --fields / --group-by / --sort-by /
        # --format table **四个旗标全部失效** —— 而"看趋势"恰恰最需要
        # `--fields collected_at,value --format table`。同一个教训我在隔壁的 topology 上写进了
        # 注释,又在这条命令里犯了一遍:贴切的名字换来一堆不工作的旗标,不划算。
        "items": payload,
        "item_kind": "metric_point",
        "summary": {
            "points": len(payload),
            "metrics": names,
            "granularity": granularities,
            "first_at": payload[0].get("collected_at") if payload else None,
            "last_at": payload[-1].get("collected_at") if payload else None,
        },
    }
    if values:
        out["summary"].update({"min": min(values), "max": max(values),
                               "first": values[0], "last": values[-1]})
    # 平台在点上标了 deprecated,而基于一个已废弃的指标做趋势判断,值得先知道这件事。
    # 不提的话,"数据齐全"和"数据齐全但这个指标已经不该用了"读起来一样。
    # ★ 点名是哪几个,不要只给一个布尔。不带 --metric-name 时这条命令会一次返回**多个指标**
    # (生产 inst19 实测 21 个),而第一版的提示写的是"**这个**指标已废弃":既指向不明,
    # 又读起来像这 21 个全废弃了。`deprecated` 是平台的**读时属性**(按 instance_type +
    # metric_name 从静态描述表算出,不存在点上),所以同名指标的每一行取值必然相同,
    # 按 metric_name 归拢即可点名。
    deprecated_names = sorted({r.get("metric_name") for r in payload
                               if isinstance(r, dict) and r.get("deprecated")})
    if deprecated_names:
        out["summary"]["deprecated"] = True
        out["summary"]["deprecated_metrics"] = deprecated_names
        out["summary"]["deprecated_note"] = (
            "平台把这 %d 个指标标记为 deprecated:%s —— 趋势本身是真的,"
            "但先确认它们还是不是你要看的那个指标。%s"
            % (len(deprecated_names), "、".join(deprecated_names),
               "(本次返回的另外 %d 个指标不受影响。)" % (len(names) - len(deprecated_names))
               if len(names) > len(deprecated_names) else "")
        )
    if args.metric_name and not payload:
        # ★ 同一个形状的第三次(alerts --severity nosuch → capacity-forecast --metric-name →
        # 这里):拼错的指标名和"指标存在但窗口内无样本"返回**完全一样**的空。
        # 而这个 helper 存在的理由,正是平台那句 "an empty list here would read as
        # 'not collected' or 'flat', which is the opposite of the truth" —— threads_running
        # 走 422 时被照顾得很好,拼错名字走空结果时一句话都没有。
        # 词表离一次 /metrics/{id}/latest 只有一步,拿来把两种空分开。
        latest = runtime["_try_get"](f"/metrics/{instance_id}/latest")
        known = sorted({r.get("metric_name") for r in latest
                        if isinstance(r, dict) and r.get("metric_name")}) \
            if isinstance(latest, list) else []
        if not known:
            # ★ 三个分支,不是两个。第一版只写了后两个,于是**词表取不到时静默落回原样** ——
            # 和修复前那个不区分的空一模一样。我在修"unknown 被当成 ok"的过程中,
            # 自己又造了一个 unknown 被当成 ok。
            # 触发时机还特别不巧:这次额外调用**只在空结果时发出**,也就是有人正在逐个试
            # 指标名的时候 —— 恰恰是最容易撞限流的场景。
            # fail-safe 的方向是"说我不知道",不是"回到不区分"。
            out["unavailable"] = True
            if runtime["_unavailable"](latest):
                out["reason"] = (
                    "空结果无法归类:取这台实例的指标词表失败(HTTP %s),所以分不清 '%s' 是"
                    "名字拼错了,还是这个指标确实在窗口内没有样本。**重试一次通常就能分清。**"
                    % (runtime["_LAST_HTTP_STATUS"], args.metric_name)
                )
            else:
                # 词表本身是空的 —— 那不是"查不到",是"这台一个指标都没采到"。
                # 和上面那种混成一句话,会让人去重试一个重试不好的问题。
                out["reason"] = (
                    "这台实例**一个指标都没有**(/latest 返回空),所以 '%s' 查不到并不说明"
                    "名字有没有拼错 —— 先查这台的采集是不是停了。" % args.metric_name
                )
        elif args.metric_name not in known:
            out["unavailable"] = True
            out["reason"] = (
                "'%s' 不在这台实例的指标词表里(它有 %d 个指标)。空结果在这里有两种含义,"
                "而它们长得一样:名字拼错了,或者这个指标确实在窗口内没有样本 —— 这是前者。"
                % (args.metric_name, len(known))
            )
            out["known_metrics"] = known
        else:
            out["summary"]["note"] = (
                "'%s' 在这台的指标词表里,但所选窗口内没有样本 —— 是**这段时间没有数据**,"
                "不是这个指标没在采。" % args.metric_name
            )
    if granularities and granularities != ["raw"]:
        # ★ "取到的是汇总"有**两个**原因,而第一版只写了其中一个:
        #   (a) 窗口超过 24 小时 —— auto 自己切的
        #   (b) 调用方自己指定了 --granularity —— 窗口可能只有 12 小时
        # 对 (b) 说"窗口超过 24 小时"是**假话**,而且后半句也跟着错:那种情况下云指标在这个
        # 窗口里恰恰是**查得到**的(raw 还在)。数据是对的,解释是错的,而错误的解释会让人
        # 去缩窗口 —— 一个不是原因的东西。
        # 这和我上一轮在 422 的 hint 上修的是同一个形状("错的建议比没有建议更贵"),
        # 只是载体换成了 note。
        if args.granularity and args.granularity != "auto":
            out["summary"]["note"] = (
                "取的是**汇总**而非原始点,因为你指定了 --granularity %s(不是因为窗口大小)。"
                "value 是该桶的均值,另有 min/max。想要原始点用 --granularity raw 或 auto"
                "(auto 在 ≤24 小时内给 raw)。" % args.granularity
            )
        else:
            out["summary"]["note"] = (
                "窗口超过 24 小时,auto 因此取了**汇总**而非原始点(value 是该桶的均值,"
                "另有 min/max)。云采集的指标没有汇总,所以它们在这个窗口里查不到 —— "
                "平台会用 422 说明,不是空数组。"
            )
    return out


def cmd_probe_run(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    if args.sql:
        runtime["_fail"](
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
    return runtime["_request"]("POST", f"/instances/{args.instance_id}/diagnostics/probe", body=body)


def cmd_prometheus_query(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    body: dict[str, Any] = {"query": args.query}
    if args.url:
        body["url"] = args.url
    return runtime["_request"]("POST", f"/instances/{args.instance_id}/prometheus/query", body=body)




def _add_diagnostic_commands(sub, runtime: dict[str, Any]) -> None:
    """诊断探针与拓扑。"""
    catalog = sub.add_parser("diagnostics-catalog")
    catalog.add_argument("--instance-id", type=int)
    catalog.set_defaults(_needs_instance=True)
    catalog.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    catalog.set_defaults(func=runtime["cmd_diagnostics_catalog"])

    run = sub.add_parser("diagnostics-run")
    run.add_argument("--instance-id", type=int)
    run.set_defaults(_needs_instance=True)
    run.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    run.add_argument("--checks", required=True)
    run.add_argument("--timeout-seconds", type=int)
    run.add_argument("--database-name")
    run.add_argument("--sql", help=runtime["argparse"].SUPPRESS)
    run.set_defaults(func=runtime["cmd_diagnostics_run"])

    probe_catalog = sub.add_parser("probe-catalog")
    probe_catalog.add_argument("--instance-id", type=int)
    probe_catalog.set_defaults(_needs_instance=True)
    probe_catalog.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    probe_catalog.set_defaults(func=runtime["cmd_probe_catalog"])

    probe_run = sub.add_parser("probe-run")
    probe_run.add_argument("--instance-id", type=int)
    probe_run.set_defaults(_needs_instance=True)
    probe_run.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    probe_run.add_argument("--probe", required=True)
    probe_run.add_argument("--sql-id")
    probe_run.add_argument("--session-id", type=int)
    probe_run.add_argument("--object-name")
    probe_run.add_argument("--sql", help=runtime["argparse"].SUPPRESS)
    probe_run.set_defaults(func=runtime["cmd_probe_run"])

    business_inference = sub.add_parser(
        "business-inference-evidence",
        help="Read persisted object-name/comment evidence for a model to infer a database's likely business",
    )
    business_inference.add_argument("--database-id", type=int)
    business_inference.add_argument("--instance-id", type=int)
    business_inference.add_argument("--database")
    business_inference.set_defaults(func=runtime["cmd_business_inference_evidence"])

    metadata_coverage = sub.add_parser(
        "metadata-coverage",
        help="Coverage and freshness of the persisted database-object directory",
    )
    metadata_coverage.set_defaults(func=runtime["cmd_metadata_coverage"])

    database_objects = sub.add_parser(
        "database-objects",
        help="Read one database's persisted object snapshot; never connects to the source database",
    )
    database_objects.add_argument("--database-id", type=int, required=True)
    database_objects.add_argument("--limit", type=int, default=100)
    database_objects.add_argument("--offset", type=int, default=0)
    database_objects.set_defaults(func=runtime["cmd_database_objects"])

    database_changes = sub.add_parser(
        "database-object-changes",
        help="Read recent persisted object additions, removals and definition/comment changes",
    )
    database_changes.add_argument("--database-id", type=int, required=True)
    database_changes.add_argument("--limit", type=int, default=100)
    database_changes.add_argument("--offset", type=int, default=0)
    database_changes.set_defaults(func=runtime["cmd_database_object_changes"])

    search_objects = sub.add_parser(
        "search-database-objects",
        help="Search persisted object names across active, non-system databases",
    )
    search_objects.add_argument("--name", required=True)
    search_objects.add_argument("--match", choices=["exact", "prefix"], default="exact")
    search_objects.add_argument("--instance-type")
    search_objects.add_argument("--object-type")
    search_objects.add_argument("--limit", type=int, default=100)
    search_objects.add_argument("--offset", type=int, default=0)
    search_objects.set_defaults(func=runtime["cmd_search_database_objects"])

    refresh_metadata = sub.add_parser(
        "refresh-database-metadata",
        help="Admin-only: queue one bounded, load-gated metadata refresh",
    )
    refresh_metadata.add_argument("--database-id", type=int, required=True)
    refresh_metadata.set_defaults(func=runtime["cmd_refresh_database_metadata"])

    refresh_status = sub.add_parser(
        "metadata-refresh-status",
        help="Read the status and completeness of one queued metadata refresh",
    )
    refresh_status.add_argument("--run-id", type=int, required=True)
    refresh_status.set_defaults(func=runtime["cmd_metadata_refresh_status"])

    propose_action = sub.add_parser(
        "propose-metadata-refresh",
        help="AI client: propose one bounded metadata refresh for independent admin approval",
    )
    propose_action.add_argument("--database-id", type=int, required=True)
    propose_action.add_argument("--reason", required=True)
    propose_action.add_argument("--evidence-ref", action="append")
    propose_action.set_defaults(func=runtime["cmd_propose_metadata_refresh"])

    action_status = sub.add_parser("action-order-status", help="Read one action order and its refresh status")
    action_status.add_argument("--order-id", type=int, required=True)
    action_status.set_defaults(func=runtime["cmd_action_order_status"])

    action_execute = sub.add_parser("execute-action-order", help="AI client: queue one independently approved order")
    action_execute.add_argument("--order-id", type=int, required=True)
    action_execute.set_defaults(func=runtime["cmd_execute_action_order"])

    action_verify = sub.add_parser("verify-action-order", help="AI client: persist server-side verification of a completed run")
    action_verify.add_argument("--order-id", type=int, required=True)
    action_verify.set_defaults(func=runtime["cmd_verify_action_order"])

    prometheus_query = sub.add_parser(
        "prometheus-query",
        help="Read-only instant PromQL against a TiDB instance's Prometheus (hotspots, "
        "golden signals, per-store flow, metric-name verification). Read-only, SSRF-guarded.",
    )
    prometheus_query.add_argument("--instance-id", type=int)
    prometheus_query.set_defaults(_needs_instance=True)
    prometheus_query.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    prometheus_query.add_argument("--query", required=True, help="a single instant PromQL expression")
    prometheus_query.add_argument("--url", help="override Prometheus URL (defaults to saved extra.prometheus_url)")
    prometheus_query.set_defaults(func=runtime["cmd_prometheus_query"])

    topo = sub.add_parser(
        "topology",
        help="复制拓扑:谁复制给谁,**含未纳管的外部主机**(/clusters 给不出这部分)",
    )
    topo.add_argument("--instance-id", type=int, help="只看这台相关的边")
    topo.add_argument("--ip")
    topo.add_argument("--host")
    topo.add_argument("--external-only", action="store_true",
                      help="只看一端是未纳管主机的边——那是 /clusters 完全看不见的部分。")
    topo.set_defaults(func=runtime["cmd_topology"])
