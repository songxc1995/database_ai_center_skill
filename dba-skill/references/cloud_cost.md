# Cloud RDS cost — reading the numbers without getting them wrong

This is the deep-water reference for `cloud-rightsizing`, `cloud-savings-realized` and
`cloud-cost-history`. SKILL.md carries only the traps you cannot afford to miss; everything
here is what you need once you are actually building a cost answer.

Every fact below was verified against the production payload on 2026-09-08, not read off the
schema. Where a number is quoted it is there to show the *shape* of the trap, not as a
current value — re-read the live response for values.

---

## 1. Two different questions people ask with the same sentence

> 降本情况如何?

- **"还能省多少"** → `cloud-rightsizing`. Candidates. Nothing here has been done.
- **"真省了多少"** → `cloud-savings-realized`. Plans acted on, in two stages.

Answering the second with the first over-reports by every candidate nobody ever executed.
If the question is ambiguous, give both and label them — they are not comparable numbers.

---

## 2. The four saving figures, and how they nest

Verified arithmetic on the live payload:

```
total_monthly_saving              = Σ saving over ALL is_candidate rows
├── realizable_monthly_saving     = actionable AND realizable_this_quarter
├── deferred_monthly_saving       = actionable AND NOT this quarter (lands at renewal)
├── pending_review_monthly_saving = needs_review (real money, held behind a human check)
└── (remainder)                   = candidates in insufficient_evidence / blocked / no_target
```

The three named sub-figures do **not** add up to the total — the remainder is real and is not
surfaced under any name of its own. On 2026-09-08 that remainder was ¥332.75 across 21 rows.
If you present a breakdown, either include the remainder or say the breakdown is partial.

**Never add `realizable` to `pending_review`** and call it "what we can save this quarter":
the second is money nobody has approved. That over-promise is the exact thing the tiering
exists to prevent.

---

## 3. Two "actionable" numbers, one payload

| Field | Means |
|---|---|
| `shortlist_states.actionable` | rows that cleared **every gate** |
| `actionable_count` | cleared every gate **AND** the saving lands this quarter |

They differ by the rows whose saving only arrives at renewal, so `actionable_count` is always
≤ the other. Neither is wrong; picking the wrong one silently changes what you claimed.

- "有多少台可以降配" → `shortlist_states.actionable`
- "这个季度能落袋多少台" → `actionable_count` (pairs with `realizable_monthly_saving`)

---

## 4. Cost vs bill — three fields that are not the same money

| Field | What it is |
|---|---|
| `monthly_cost` | **catalogue price** at contract discount. Same basis as `monthly_saving`, which is why saving ≤ cost holds |
| `real_monthly_bill` | what the **invoice** said for one instance, last closed cycle |
| `total_real_bill` | fleet sum of the above, **run-rate rows only** |

包年包月 is billed as a whole term up front, not amortised, so an instance that did not renew
this cycle shows a real bill of ¥0 while genuinely costing money. That is why the platform
prices from the catalogue and keeps the invoice as a reference column — do not "correct" one
with the other.

### `real_monthly_bill = ¥0` has **three** meanings

| Which | How to tell | What it means for cost |
|---|---|---|
| Fully covered by a voucher | `coupon_amount > 0`, `bill_coupon_funded: true` | **Un-surfaced cash exposure** — becomes cash when the voucher runs out |
| Not billed this cycle | no coupon, no bill row | 包年包月 that did not renew, or a collection gap |
| Refund cycle (**negative**) | `real_monthly_bill < 0`, `bill_is_run_rate: false` | one-off refund from a downsize, not a monthly rate |

`bill_is_run_rate` is the platform's own verdict on this — read it instead of re-deriving.

**Coverage limit:** per-instance voucher data exists for **Huawei only**. Aliyun accounts for
vouchers at account/cycle level, so an empty `coupon_funded` list does **not** mean no Aliyun
instance is voucher-covered. `coupon_funded.note` says this in the response; quote it.

---

## 5. Savings are compute-only. Storage is a separate, unclaimed pool

`monthly_saving` comes from changing the instance **class**. Storage is not resized by a class
change, so over-provisioned disk never appears in any saving figure.

`storage_summary` carries it: `allocated_gb`, `used_gb`, `utilization_pct`,
`over_provisioned_count` and a sample list. On 2026-09-08 the fleet was 19,235 GB allocated
against 6,891 GB used (35.8%), 39 instances over-provisioned — none of which is in
`total_monthly_saving`.

If someone asks "还能省多少", a complete answer names both pools and says they are separate.

---

## 6. Cost numbers drift — carry `cost_refreshed_at`

Cost and recommendation columns come from a daily refresh, not from the request. Between two
reads days apart the totals move even when the fleet did not change: re-pricing, new bills,
a target class becoming orderable.

**Carry `cost_refreshed_at` with any conclusion built on these numbers**, the same way
`generated_at` is carried for verdicts. Also read:

- `cost_refresh_running` / `cost_refresh_phase` — a refresh is in flight, so figures are mid-update
- `cost_refresh_throttled` — rows that kept their previous values because the vendor rate-limited
- `cost_refresh_interrupted` — a previous run was killed (almost always a restart)

A throttled row is **not** a row with no downsize target. Saying "no target" on the strength
of a rate limit is the mislabel this field exists to prevent.

---

## 7. `shortlist_state` — what each verdict actually claims

| State | Claim |
|---|---|
| `actionable` | cleared every gate; safe to act on |
| `needs_review` | real saving, held behind a mandatory human check |
| `blocked` | evaluated and **refused** (memory projection, floor, …) |
| `no_target` | could **not** be evaluated, or the answer is ambiguous |
| `insufficient_evidence` | metric window too short or does not span a month-end |
| `not_candidate` | not over-provisioned |

`blocked` and `no_target` are opposite claims and are easy to conflate: the first is a
conclusion, the second is an absence. Read `headroom_blockers` for the sentence — since
v3.62.0 there are seven distinct ones, and **"当前规格不在厂商目录清单里" is a collection gap
that needs chasing**, while "可降档清单为空" on a 2-core instance is a normal result.

### Month-end review

An instance whose name or database names match settlement vocabulary (invoice / finance /
tax / expense / settle) is *triggered* for review, then **answered by measurement**: if the
metric window fully covers ≥2 month-end settlement windows and the measured month-end CPU
peak projects under the ceiling, it is released and `periodic_review_reason` says so with the
numbers. `month_end_peak_cpu_pct` and `month_ends_covered` are on every row.

An explicit `periodic_workload` tag always outranks measurement — a human marked it knowing
something the metrics do not show.

---

## 8. Realized savings: two stages, and why ¥0 is usually correct

`cloud-savings-realized` returns plans, not candidates.

| Status | Means |
|---|---|
| `candidate` | proposed, nobody decided |
| `adopted` | approved, snapshot frozen |
| `downsized` | the class actually changed — observable today |
| `verified` | **an invoice came in below the pre-change baseline** — real money |
| `rejected` / `superseded` | turned down, or replaced by a newer proposal |

`verified_monthly_saving = ¥0` is the normal state for this fleet, not a broken pipeline:
almost every instance is 包年包月 and a subscription re-prices **only at renewal**. The
response says which wait you are looking at:

| `verification_basis` | Means |
|---|---|
| `subscription_renewal` | waiting for renewal; `verification_expected_at` has the date |
| `next_billing_cycle` | pay-as-you-go, next cycle shows it |
| `waiting_clean_cycle` | the latest bill is a **refund cycle**, not a run rate |
| `unverifiable_no_baseline` | **will never verify by itself** — the pre-change bill was never captured |
| `unknown_missing_expire_time` | 包年包月 with no recorded expiry |

`unverifiable_no_baseline` is the one to call out: it is not queued, it is stuck, and only a
manual invoice comparison closes it.

`drift_note` means the live report has moved away from the snapshot a human approved. Shown,
never auto-applied — but do not leave it buried; `cloud-savings-realized` lifts drifted plans
to the top level.

---

## 9. `cloud-cost-history` is **Aliyun only**

There is no Huawei 5-year billing overview API, so this series is not the fleet's total spend
and there is no vendor dimension in it at all — which is why the command has no `--vendor`
flag. One would return Aliyun numbers under a Huawei label.

Amounts: `gross` = 原价 (list price, stable trend), `paid` = 应付 = the real net cost
**already net of contract discount and vouchers**, `coupon` = how much voucher was applied.
Do not subtract `coupon` from `paid` — that double-counts it.

`--since-cycle` / `--until-cycle` window the monthly series; `--yoy` adds a per-year delta and
percentage against the previous year (undefined, reported as `null`, when the prior year is 0
— a `0%` there would read as "unchanged").

**`--yoy` compares on `net_consumption` = `paid + coupon`. Neither `paid` nor `gross` alone
is safe.** Measured on the production series:

| 年 | gross 同比 | paid 同比 | **net = paid+券** | 真相 |
|---|---|---|---|---|
| 2024 | +1.8% | −8.5% | **−8.6%** | 三者一致 |
| 2025 | +0.5% | −67.1% | **−5.7%** | 消耗降了 5.7%,paid 说崩了、gross 说持平,都错 |
| 2026 | −42.4% | +40.9% | **−42.7%** | 确实在降;paid 的"暴涨"只是券见底 |

- `paid` 是**扣券后的现金**:券多的年份看着暴跌,券用尽的年份看着暴涨。
- `gross` 是**目录价**(折扣前):它看不见折扣率的变化。2025 有效折扣从 47.8% 变到 44.9%,
  于是「我们谈到了更好的价」被 gross 算成了「没变化」。阿里账单口径那份参考里也写着
  gross ≈ 应付的 2 倍、**虚高别用**。
- `net = paid + coupon` = 原价 − 折扣 − 舍入:**券在里面互相抵消,合同折扣已经生效** ——
  这才是「按我们实际谈到的价格,消耗了多少」。

`gross_pct` 和 `paid_pct` 仍并排给出并单独具名 —— 「目录价趋势」和「现金支出」都是真问题,
只是都不是**那个**趋势。响应里的 `year_on_year_basis` 带着这句说明。

**任何不满 12 个月的年份标 `partial`,并给出 `partial_reason`。** 三种情况处置完全不同,
合成一个 `partial` 会把第三种伪装成前两种:

| reason | 含义 | 该做什么 |
|---|---|---|
| `series_start` | 数据起点(2021 从 8 月起) | 没事,**永远不会补齐**,别等 |
| `year_in_progress` | 今年还没过完 | 没事,会自己补齐 |
| **`missing_months`** | **中间年份缺月** | **★ 账单数据缺口,去查** |

覆盖月数从**未窗口化**的月序列统计,所以 `--since-cycle` 只收窄月度行,不会把窗口内每一年
都变成假的 `partial`(已实跑验证:`--since-cycle 2026-01 --until-cycle 2026-08` 下
`months_filtered_by.kept=8`,而 2026 的 `months_covered` 仍是 9)。

---

## 10. Reading a large response without dumping it

`cloud-rightsizing` is ~300 KB. Do not pull it whole and then hand-parse.

```
--summary-only                                   # scalars only, ~2 KB — the headline
--fields name,monthly_saving,shortlist_state --format table
--group-by shortlist_state                       # counts per state
--sort-by monthly_saving --desc                  # biggest first
```

`--count-only` drops only **top-level** lists, so this response still comes back ~10 KB with
it (its headline objects carry their own lists). `--summary-only` is the one that means
"just the numbers".

**Read the top level first, then decide whether you need rows.** Top-level fields are added
as the platform evolves — `coupon_funded` and `refund_cycles` arrived in v3.62.0 — so a
hard-coded field list goes stale. `--summary-only` shows you everything that is there now.
