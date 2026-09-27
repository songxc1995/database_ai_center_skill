"""Cost command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_capacity_forecast(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Projected resource exhaustion — including trends too far out to alert.

    ``would_alert`` is reported per row rather than used as a filter: a tablespace 200 days
    from full never appears in the alert list, and that lead time is the whole point. Pass
    ``--include-gaps`` to also see the instances that could NOT be projected, with reasons —
    an instance missing from the list is not the same as an instance with no risk.
    """
    return runtime["_request"](
        "GET",
        "/dba/capacity/forecast",
        params={
            "metric_name": args.metric_name,
            "max_days": args.max_days,
            "include_gaps": "true" if args.include_gaps else None,
            "limit": args.limit,
        },
    )


def cmd_cloud_rightsizing(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/cloud-rds/rightsizing",
        params={
            "window_days": args.window_days,
            "cpu_max": args.cpu_max,
            "mem_max": args.mem_max,
            "vendor": args.vendor,
        },
    )


def cmd_cloud_monitoring_coverage(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/dba/cloud-rds/monitoring-coverage",
        params={
            "vendor": args.vendor,
            "coverage": "missing" if args.missing_only else args.coverage,
            "tenant_id": args.tenant_id,
            "limit": args.limit,
            "offset": args.offset,
        },
    )


def cmd_cloud_savings_realized(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """What was actually DONE about cost, not what could be.

    ``cloud-rightsizing`` answers "how much could we save" (candidates). This answers "how much
    did we save" (plans acted on) — two different questions that people ask with the same
    sentence, 降本情况如何. Answering the second with the first over-reports by every candidate
    nobody ever executed.

    The two-stage split is the whole point and is easy to misread:
      * ``downsized``  = the class changed. Observable today.
      * ``verified``   = an invoice came in below the pre-change baseline. Real money.
    Almost this fleet is 包年包月, and a subscription re-prices **only at renewal** — so a plan
    sitting at ``downsized`` for months is not stalled, and ``verified_monthly_saving = ¥0`` is
    not a broken pipeline. ``verification_note`` says which of those it is; it is lifted to the
    top here rather than left in the payload, because without it "账单没降" reads as failure.
    """
    payload = runtime["_request"](
        "GET", "/cloud-rds/downsizing-plans",
        params={"status": args.status, "sort": args.sort},
    )
    if not isinstance(payload, dict):
        return payload
    items = payload.get("items")
    if not isinstance(items, list):
        return payload
    if args.pending_only:
        items = [p for p in items if p.get("status") in ("adopted", "downsized")]
    if args.drifted_only:
        items = [p for p in items if p.get("drift_note")]
    payload = {**payload, "items": items}
    # A per-status roll-up beside the rows, so the first question ("how many are where")
    # does not need a hand-written pass over the list.
    by_status: dict[str, int] = {}
    for plan in items:
        key = str(plan.get("status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1
    payload["by_status"] = by_status
    # Why the verified figure is what it is. A bare ¥0 and a broken verification chain look
    # identical; these say which one you are looking at.
    waiting: dict[str, int] = {}
    for plan in items:
        if plan.get("status") in ("adopted", "downsized"):
            basis = str(plan.get("verification_basis") or "unknown")
            waiting[basis] = waiting.get(basis, 0) + 1
    if waiting:
        payload["awaiting_verification_by_basis"] = waiting
    drifted = [p for p in items if p.get("drift_note")]
    if drifted:
        # Drift = the live report has moved away from the snapshot a human approved. Shown,
        # never auto-applied — but it must not stay buried in a per-row field either.
        payload["drifted"] = [
            {"id": p.get("id"), "instance_name": p.get("instance_name"),
             "status": p.get("status"), "drift_note": p.get("drift_note")}
            for p in drifted
        ]
    return payload


def cmd_cloud_cost_history(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Billing history. **Aliyun only** — Huawei has no 5-year overview API, so this is not
    the fleet's total spend, and reading it as such under-reports by every Huawei instance.

    The endpoint itself takes no parameters (it returns the whole stored series), so the
    windowing and the year-on-year comparison below are done here on the full payload rather
    than pushed to the server. That is a deliberate limit, not an oversight: there is no
    vendor dimension in this data at all, which is why there is no ``--vendor`` flag — one
    would return Aliyun numbers under a Huawei label.
    """
    payload = runtime["_request"]("GET", "/cloud-rds/cost-history")
    if not isinstance(payload, dict):
        return payload
    if "coverage" not in payload:
        # 老平台(< v3.63.0)不报覆盖范围。这里补一句**已知的限制**,而不是让调用方以为
        # 这是全舰队支出。刻意不写死 "aliyun_only":平台自己会算的那份才是权威,一旦它开始
        # 返回 coverage,这段就让位——写死的文案是会过期的,而没人会记得回来改。
        payload = {**payload, "coverage": {
            "vendors_included": None,
            "vendors_missing": None,
            "note": "平台未返回覆盖范围(旧版本)。已知限制:华为没有 5 年账单总览接口,"
                    "所以这份历史很可能不含华为支出——按全舰队解读会低估。",
        }}
    months = payload.get("months")
    if isinstance(months, list) and (args.since_cycle or args.until_cycle):
        lo, hi = args.since_cycle, args.until_cycle
        kept = [m for m in months
                if (not lo or str(m.get("cycle") or "") >= lo)
                and (not hi or str(m.get("cycle") or "") <= hi)]
        payload["months"] = kept
        payload["months_filtered_by"] = {"since_cycle": lo, "until_cycle": hi,
                                         "kept": len(kept), "of": len(months)}
    if args.yoy:
        # `months` still references the **unwindowed** series even after the filter above
        # replaced payload["months"] — year coverage must not shrink just because the caller
        # asked for a narrower monthly view.
        payload["year_on_year"] = runtime["_year_on_year"](
            payload.get("years"), months if isinstance(months, list) else None
        )
        payload["year_on_year_basis"] = (
            "pct/delta 按 **net_consumption = paid + coupon**(= 原价−折扣−舍入,按我们实际"
            "谈到的价格消耗了多少)。gross_pct 是目录价口径,看不见折扣率的变化;"
            "paid_pct 是现金口径,受代金券时机扭曲——券多的年份看着暴跌、券用尽的年份看着暴涨。"
            "三者都给出但只有 pct 是趋势。"
            "partial=true 的年份未满 12 个月;partial_reason 区分"
            "series_start(数据起点,永不补齐)/ year_in_progress(会自己补齐)/ "
            "**missing_months(中间年份缺月 = 账单数据缺口,要去查)**。"
            "★ 这种年份的 pct/delta 已改为**同月对同月**(pct_basis=same_months,"
            "compared_months 列出是哪几个月),因为拿 9 个月比整年光月份数就有约 −25% 的"
            "固定偏差、根本不是趋势;年合计对整年的那个数保留为 full_year_pct/full_year_delta,"
            "它是事实但不能当趋势读。去年缺少对应月份时无法同月对比,标 pct_basis=unequal_months。"
        )
    return payload




def _add_capacity_commands(sub, runtime: dict[str, Any]) -> None:
    """容量与指标。"""
    capf = sub.add_parser(
        "capacity-forecast",
        help="Projected exhaustion incl. trends below the alert threshold (v3.32+)",
    )
    capf.add_argument("--metric-name")
    capf.add_argument("--max-days", type=float)
    capf.add_argument("--include-gaps", action="store_true")
    capf.add_argument("--limit", type=runtime["_positive_int"], default=500)
    capf.set_defaults(func=runtime["cmd_capacity_forecast"])

    mseries = sub.add_parser(
        "metric-series",
        help="一个指标的走势(latest 只给一个点)。★ >24h 会切汇总表,云采集指标在那儿没有数据",
    )
    mseries.add_argument("--instance-id", type=int)
    mseries.add_argument("--ip")
    mseries.add_argument("--host")
    mseries.add_argument("--metric-name", help="不传则返回全部指标——通常很大,建议指定")
    mseries.add_argument("--hours", type=int, default=24,
                         help="回看窗口,默认 24(★ 超过 24 会切到汇总表)")
    mseries.add_argument("--granularity", choices=["auto", "raw", "minute", "hour", "day"],
                         help="默认 auto:≤24h 用 raw,再往上依次 minute/hour/day")
    mseries.set_defaults(func=runtime["cmd_metric_series"])


def _add_cost_commands(sub, runtime: dict[str, Any]) -> None:
    """云成本。"""
    cloud_monitoring = sub.add_parser(
        "cloud-monitoring-coverage",
        help="Which cloud RDS instances have DB-connection deep monitoring, and which do not",
    )
    cloud_monitoring.add_argument("--vendor", choices=["aliyun", "huawei"], default="aliyun")
    mode = cloud_monitoring.add_mutually_exclusive_group()
    mode.add_argument("--coverage", choices=["all", "enabled", "missing"], default="all")
    mode.add_argument(
        "--missing-only",
        action="store_true",
        help="Only instances without deep monitoring (shortcut for --coverage missing)",
    )
    cloud_monitoring.add_argument("--tenant-id")
    cloud_monitoring.add_argument("--limit", type=runtime["_positive_int"], default=2000)
    cloud_monitoring.add_argument("--offset", type=int)
    cloud_monitoring.set_defaults(func=runtime["cmd_cloud_monitoring_coverage"])

    cloud_rightsizing = sub.add_parser(
        "cloud-rightsizing",
        help="Cloud RDS right-sizing readout: per-instance peaks, downsize candidates, cost + saving (v2.74+)",
    )
    cloud_rightsizing.add_argument("--window-days", type=int, help="Trailing peak window (1-90, default 90)")
    cloud_rightsizing.add_argument("--cpu-max", type=float, help="Candidate CPU ceiling %% (default 40)")
    cloud_rightsizing.add_argument("--mem-max", type=float, help="Memory-pressure impediment %% (default 70)")
    cloud_rightsizing.add_argument("--vendor", choices=["aliyun", "huawei"], help="Restrict to one provider")
    cloud_rightsizing.set_defaults(func=runtime["cmd_cloud_rightsizing"])

    cloud_savings = sub.add_parser(
        "cloud-savings-realized",
        help="降本成效:已执行的降配/退订计划(不是候选)。二阶段=downsized(规格已变)/verified(账单已降)",
    )
    cloud_savings.add_argument("--status", choices=["candidate", "adopted", "downsized",
                                                    "verified", "rejected", "superseded"],
                               help="Filter by plan status; omit for all.")
    cloud_savings.add_argument("--pending-only", action="store_true",
                               help="Only plans done but not yet invoice-verified (adopted/downsized).")
    cloud_savings.add_argument("--drifted-only", action="store_true",
                               help="Only plans whose live report has moved away from the approved snapshot.")
    cloud_savings.add_argument("--sort", default="saving_desc",
                               help="saving_desc | saving_asc | cost_desc | name | status | updated_desc")
    cloud_savings.set_defaults(func=runtime["cmd_cloud_savings_realized"])

    cloud_cost_history = sub.add_parser(
        "cloud-cost-history",
        help="Cloud RDS billing history (gross / paid / coupon by month + year). ALIYUN ONLY.",
    )
    cloud_cost_history.add_argument("--since-cycle", metavar="YYYY-MM",
                                    help="Keep months at or after this billing cycle.")
    cloud_cost_history.add_argument("--until-cycle", metavar="YYYY-MM",
                                    help="Keep months at or before this billing cycle.")
    cloud_cost_history.add_argument("--yoy", action="store_true",
                                    help="Add per-year delta and %% vs the previous year, on "
                                         "gross (list price). paid_pct is reported separately: "
                                         "it is net of vouchers and inverts the real trend in "
                                         "coupon-heavy years. Partial years are flagged.")
    cloud_cost_history.set_defaults(func=runtime["cmd_cloud_cost_history"])


def _year_on_year(years: Any, months: Any = None, *, runtime: dict[str, Any]) -> Any:
    """Δ vs the previous year, per year. Hand-computing this was the most repeated follow-up
    to this command.

    ``pct`` follows **net consumption = paid + coupon**, which is 原价 − 折扣 − 舍入: what the
    fleet consumed at the prices we actually negotiated.

    Neither of the two obvious candidates is right on its own:

    * ``paid`` is already net of vouchers, so a year that burns a large coupon balance reads as
      a collapse and the year the coupons run out reads as a surge. Production 2025: paid
      −67.1% while consumption barely moved.
    * ``gross`` is **list price**, before contract discount, so it calls a better-negotiated
      rate "no change". Production 2025: gross +0.5% while net consumption fell 5.7% — the
      effective discount had moved from 47.8% to 44.9%, and gross cannot see that.

    Net consumption is immune to both: vouchers cancel out (they are inside it), and the
    contract discount is already applied. ``gross_pct`` and ``paid_pct`` are reported beside
    it, separately named, because "what was the list price trend" and "what did we pay in
    cash" are both real questions — they are just not *the* trend.

    A year still in progress is marked ``partial`` with its ``months_covered``. Comparing 8
    months against 12 is not a −33% trend, and the caller cannot see the month count from the
    yearly totals alone. Coverage is counted from the **unwindowed** month series, so
    ``--since-cycle`` / ``--until-cycle`` narrow the monthly rows without silently turning
    every year in the window into a fake "partial".
    """
    if not isinstance(years, list):
        return None
    # 每年出现过哪几个**月份号**,而不只是数量 —— 数量分不出「8-12 连续」和「8,9,11,12 有洞」,
    # 而后者才是真正的账单缺口。
    seen: dict[str, set[int]] = {}
    # 同月对同月要用到逐月净额 —— 不满 12 个月的年份,拿年合计比整年得到的不是趋势。
    monthly_net: dict[str, dict[int, float]] = {}
    # gross / paid 也要逐月:同月对比必须**三组一起**做,否则同一行里混着两种口径。
    monthly_gross: dict[str, dict[int, float]] = {}
    monthly_paid: dict[str, dict[int, float]] = {}
    have_months = isinstance(months, list) and bool(months)
    if have_months:
        for m in months:
            if isinstance(m, dict):
                cycle = str(m.get("cycle") or "")
                if len(cycle) >= 7 and cycle[4] == "-" and cycle[5:7].isdigit():
                    y, mm = cycle[:4], int(cycle[5:7])
                    seen.setdefault(y, set()).add(mm)
                    paid, coupon, gross_m = m.get("paid"), m.get("coupon"), m.get("gross")
                    if isinstance(paid, (int, float)):
                        monthly_net.setdefault(y, {})[mm] = paid + (
                            coupon if isinstance(coupon, (int, float)) else 0.0)
                        monthly_paid.setdefault(y, {})[mm] = paid
                    if isinstance(gross_m, (int, float)):
                        monthly_gross.setdefault(y, {})[mm] = gross_m

    def _coverage(year: str) -> tuple[int, str | None]:
        """(月数, partial 的原因)。原因为 None 表示这一年是完整的。

        判据是**上下界分别解释**,不是两条各管一头的规则。前一版写成「当前年且从 1 月起」和
        「起点年且到 12 月止」两条,当一年**同时是**起点年和当前年时(今年年中才接入的部署,
        在它的第一个自然年内)——它既到不了 12 月、也不从 1 月起,两条都不命中,于是掉进兜底
        的 missing_months,把一个完全正常的新部署报成账单缺口。本项目自己就差点是这个形状:
        数据起点 2021-08。

        所以改成分别问两个问题:**缺的那段月份,有没有一个不是缺口的解释?**
          * 下界:从 1 月起,或者这就是序列的起点年(之前本来就没有数据)
          * 上界:到 12 月止,或者这就是当前年(年还没过完,之后本来就还没发生)
        两头都有解释才不是缺口;任何一头解释不了,就是真的少了账期。

        ★ ``seen.get(year, set())`` 取的是**空集**而不是 None:years 里有某年、months 里
        一个月都没有 —— 那是最极端的账单缺口,而它曾经是唯一连 partial 都不标的一种,
        读起来跟"完整"一模一样。
        """
        got = seen.get(year, set())
        n = len(got)
        if n >= 12:
            return n, None
        if not got:
            return 0, "missing_months"              # 整年缺失
        lo, hi = min(got), max(got)
        if hi - lo + 1 != n:
            return n, "missing_months"              # 中间有洞,与是不是首/末年无关
        is_first, is_now = year == earliest, year == current_year
        lo_ok = lo == 1 or is_first                 # 序列就从这儿开始,之前不算"缺"
        # ★ 当前年的上界必须和**现在到哪个月了**比,不能只凭"是今年"就放行。
        # 写成 `hi == 12 or is_now` 时,is_now 是一张无条件通行证:今年的账单从 3 月起就断了,
        # 缺了 4–9 月,照样被说成"年还没过完"—— 真缺口被吸收成正常在途,而这正是本项目
        # 最贵的那类错。留一个月余量:当月账期可能还没出账(本部署 09-08 就已有 2026-09,
        # 出账很快;慢的凭据需要这一格)。
        hi_ok = hi == 12 or (is_now and hi >= current_month - 1)
        if lo_ok and hi_ok:
            # ★ 按**实际用到了哪条放宽**贴标签,不按"哪个标志为真"。
            # 一个只有 2026 一年的序列,它既是 earliest 也是 current_year,但如果它从 1 月起,
            # 下界压根不需要"起点年"这条豁免 —— 标成 series_start_in_progress 就是在说
            # "早于起点的月份永不补齐",而它根本没有更早的月份。这是同一个洞的第三次:
            # 前两次是两条规则各管一头留下缝隙,这次是标签读的是标志而不是理由。
            used_start = lo != 1        # 下界靠"这是序列起点年"才成立
            used_progress = hi != 12    # 上界靠"今年还没过完"才成立
            if used_start and used_progress:
                return n, "series_start_in_progress"
            return n, "year_in_progress" if used_progress else "series_start"
        # 账单是滞后出账的:跨年那几周,去年合法地还缺 12 月。仍然标 partial(不隐藏),
        # 但给它自己的名字 —— 报成 missing_months 会让人每年年初白查一趟。
        # 只对**当前年的前一年、只差 12 月、且现在还在 1–2 月**放行,窗口刻意开得很窄。
        try:
            prev_year = int(current_year) - 1 == int(year)
        except (TypeError, ValueError):
            prev_year = False
        # ★ 这里用 lo_ok 而不是 lo == 1:年中接入的部署永远满足不了 lo == 1,于是它的
        # "去年"每逢年初都会被报成缺口 —— 和 FP-1 同一个洞(又一条规则默认序列从 1 月起)。
        if prev_year and lo_ok and hi == 11 and current_month <= 2:
            return n, "awaiting_final_cycle"
        return n, "missing_months"                  # 有一头解释不了 = 真的少了账期
    # ★ 按年份**排序**再算,不依赖服务端的返回顺序。位置式的 [0]/[-1] 在倒序返回时会把
    # series_start 和 year_in_progress 直接对调 —— 两个都错、方向相反、都是误导;而 prev_*
    # 的累加同样依赖顺序,倒序会让整个同比失真。对客户端不控制的数据做无防御的顺序假设,
    # 是这一整类缺陷的共同根子。
    years = sorted(
        [r for r in years if isinstance(r, dict)],
        key=lambda r: str(r.get("year") or ""),
    )
    known_years = [str(r.get("year")) for r in years]
    earliest = min(known_years) if known_years else None
    _now = runtime["datetime"].now()
    current_year, current_month = str(_now.year), _now.month

    def _pct(cur: Any, prev: Any) -> tuple[Any, Any]:
        if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
            return None, None
        # 上一年为 0 时同比无定义 —— 报 None 而不是 0%,后者读起来像"没变化"。
        return round(cur - prev, 2), (round((cur - prev) / prev * 100, 1) if prev else None)

    out = []
    prev_gross = prev_paid = prev_net = None
    for row in years:
        if not isinstance(row, dict):
            continue
        year = str(row.get("year"))
        gross, paid, coupon = row.get("gross"), row.get("paid"), row.get("coupon")
        net = (paid + coupon) if isinstance(paid, (int, float)) and isinstance(coupon, (int, float)) else paid
        entry = {"year": row.get("year"), "gross": gross, "paid": paid, "coupon": coupon,
                 "net_consumption": round(net, 2) if isinstance(net, (int, float)) else None}
        if have_months:
            n, reason = _coverage(year)
            entry["months_covered"] = n
            if reason:
                entry["partial"] = True
                entry["partial_reason"] = reason
        else:
            # 没有月度序列就**无法**判断完整性。此前这种情况下整个 partial 机制静默消失,
            # 每一年都读起来像完整的 —— "查不了"和"没问题"必须是两种可见的答案。
            entry["coverage_unknown"] = True
            entry["coverage_unknown_reason"] = (
                "响应里没有 months 序列,无法判断该年是否满 12 个月;partial 未作判定"
            )
        entry["delta"], entry["pct"] = _pct(net, prev_net)
        entry["gross_delta"], entry["gross_pct"] = _pct(gross, prev_gross)
        entry["paid_delta"], entry["paid_pct"] = _pct(paid, prev_paid)
        entry["pct_basis"] = "full_year"
        # ★ 不满 12 个月的年份,年合计比整年**根本不是趋势** —— 光月份数就带来固定偏差
        #   (9 比 12,持平的一年也会显示 −25%)。这个 docstring 上面自己写着,而代码照样
        #   算了那个数、只在旁边挂一句 partial 的告诫 —— 又是"注释比实现更正确"。
        #   而这件事有确切答案:拿去年**同样这几个月**比。能算准的问题不该留给读者去折算。
        #   原来那个整年数不丢,改名 full_year_*:它是事实,只是不能当趋势读。
        if entry.get("partial") and have_months:
            cur_months = seen.get(year) or set()
            try:
                prev_y = str(int(year) - 1)
            except (TypeError, ValueError):
                prev_y = ""
            prev_have = seen.get(prev_y) or set()
            if cur_months and cur_months <= prev_have:
                cur_sum = sum(monthly_net.get(year, {}).get(mm, 0.0) for mm in cur_months)
                prev_sum = sum(monthly_net.get(prev_y, {}).get(mm, 0.0) for mm in cur_months)
                lfl_delta, lfl_pct = _pct(round(cur_sum, 2), round(prev_sum, 2))
                entry["full_year_delta"], entry["full_year_pct"] = entry["delta"], entry["pct"]
                entry["delta"], entry["pct"] = lfl_delta, lfl_pct
                entry["pct_basis"] = "same_months"
                entry["compared_months"] = sorted(cur_months)
                # ★ 三组必须同一个口径。a348a32 只把 net 这组换成了同月,gross / paid 两组仍是
                #   9 个月比 12 个月,pct_note 却一个字不提 —— 生产 2026:gross_pct 读作 −40.7%,
                #   同月真值 −24.3%,差 16 个百分点(2026-09-14 测评 P0)。同一行里混两种口径比
                #   全错更难发现:net 那个数是对的,读的人会默认旁边的也对。
                missing_groups = []
                for label, monthly in (("gross", monthly_gross), ("paid", monthly_paid)):
                    entry["full_year_%s_delta" % label] = entry["%s_delta" % label]
                    entry["full_year_%s_pct" % label] = entry["%s_pct" % label]
                    cur_m, prev_m = monthly.get(year, {}), monthly.get(prev_y, {})
                    if all(mm in cur_m and mm in prev_m for mm in cur_months):
                        entry["%s_delta" % label], entry["%s_pct" % label] = _pct(
                            round(sum(cur_m[mm] for mm in cur_months), 2),
                            round(sum(prev_m[mm] for mm in cur_months), 2))
                    else:
                        # 缺逐月数据就算不出同月值 —— 置空并说出来,绝不悄悄留下整年那个数。
                        entry["%s_delta" % label] = entry["%s_pct" % label] = None
                        missing_groups.append(label)
                entry["pct_note"] = (
                    "本年只有 %d 个月,所以 pct/delta、gross_pct/gross_delta、paid_pct/paid_delta "
                    "三组都是拿 %s 年**同样这几个月**比出来的。full_year_* 是年合计对整年,"
                    "含 %d 个月的固定偏差,不能当趋势读。"
                    % (len(cur_months), prev_y, 12 - len(cur_months))
                )
                if missing_groups:
                    entry["pct_note"] += "(%s 缺逐月数据,无法同月对比,已置空)" % "、".join(missing_groups)
            elif not prev_have:
                # ★ 序列起点年:上一年**根本不存在**(0 个月),不是"缺了其中某些月份"。
                #   说成缺月会让人去找一批不存在的账单 —— 诊断不同,动作也不同。
                #   而这一行的 partial_reason 早就正确地写着 series_start:同一行里两个字段
                #   给出矛盾的诊断,是我自己造的。生产第一行(2021)走的就是这条。
                #   pct/delta 为 None 也要说清是哪一种 None:不是数据缺失,是没有可比对象。
                entry["pct_basis"] = "no_prior_year"
                entry["pct_note"] = (
                    "序列从这一年开始(上一年在账单数据里一个月都没有),**没有可比的上一年** —— "
                    "pct/delta 为 null 不是数据缺失,是这个问题在这一年没有答案。"
                )
            else:
                # 上一年存在,但缺了本年有的某些月份 —— 这才是真的同月对不上。
                entry["pct_basis"] = "unequal_months"
                entry["missing_in_prior_year"] = sorted(cur_months - prev_have)
                entry["pct_note"] = (
                    "本年不满 12 个月,而上一年缺少其中的 %s 月,**无法同月对比**;"
                    "这里的 pct、gross_pct、paid_pct 都是年合计对整年,含月份数差带来的固定偏差,不是趋势。"
                    % "、".join(str(m) for m in sorted(cur_months - prev_have))
                )
        if isinstance(gross, (int, float)):
            prev_gross = gross
        if isinstance(paid, (int, float)):
            prev_paid = paid
        if isinstance(net, (int, float)):
            prev_net = net
        out.append(entry)
    return out
