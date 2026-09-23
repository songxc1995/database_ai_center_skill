# Database AI Center Skills

This repository contains skills for external model platforms that can make outbound HTTP requests.

## Skills

- `dba-skill/`
  - Replaces the former `database-ai-center/` alert-only skill.
  - Uses Database AI Center `v2.0.21+` `/api/v2/dba/*` APIs (plus the read-only `GET /alerts` and `GET /instances/classification` — the latter needs `v2.0.76+`).
  - Answers database estate statistics, unused database, inactive discovery, ownership, contact, department, business/application, current alerts, instance topology classification (RAC / Data Guard / replication / standalone / cloud RDS / has-backup), alert evidence, freshness, timeline, and allowlisted diagnostic questions.
  - **Live evidence drill-down (`v2.19.0+`):** runs the read-only, whitelisted diagnostic probes the agentic AI pipeline uses — `probe-catalog` / `probe-run` over `GET|POST /instances/{id}/diagnostics/{catalog,probe}` — for true multi-round root-cause analysis (slow_queries → sql_plan → index_coverage + table_stats → bind_values). Caller passes a probe **name** + bound params (`--sql-id` / `--session-id` / `--object-name`), never SQL; the fixed SQL stays server-side and output is redacted. Requires `AI_DIAGNOSTIC_PROBES_ENABLED=true` and role `ai-client` (or higher).
  - **Knowledge base grounding (`v2.32+`):** `kb-search` / `kb-incidents` / `kb-doc-search` over `GET /knowledge/entries`, `.../incidents`, and `GET /knowledge/documents/search` — pull DBA-confirmed symptom→root-cause→remediation history and curated ops-runbook passages to ground a diagnosis in prior incidents. Read-only (`viewer`+); degrades to empty when the RAG corpus is absent.
  - Includes `scripts/dba_api_client.py` to make common DBA API calls safely.
  - Optionally enriches analysis with `zabbix-readonly` for host-side evidence.

- `zabbix-readonly/`
  - Read-only Zabbix helper skill.
  - Resolves a host by IP, optionally disambiguates by host name.
  - Returns structured host-side performance evidence.

## Runtime Config

DBA Skill:

```env
PROJECT_API_BASE_URL=http://your-database-ai-center/api/v2
PROJECT_API_KEY=replace-with-real-api-key
PROJECT_TIMEOUT_SECONDS=15
PROJECT_STALE_AFTER_HOURS=72
```

### AgentMesh STDIO MCP（先接入 MCP，不要求 AgentMesh 执行 Skill 脚本）

`dba-skill/mcp_server.py` 是独立于平台后端的 STDIO MCP 入口。它复用现有 DBA 客户端的
脱敏、端点发现与操作单调用，不直接连接业务数据库。需将本仓库的 `dba-skill/` 目录放在
**AgentMesh 实际运行数字员工进程的机器或容器内**；只在管理页面填写本机不存在的路径不会启动。

在该运行环境用 Python 3.10+ 安装 `requirements-mcp.txt`，然后在 AgentMesh 的「添加 MCP
服务器」里选择 `STDIO`：

```text
服务器名称: database-ai-center
命令: /absolute/path/to/python /absolute/path/to/dba-skill/mcp_server.py
环境变量: PROJECT_API_BASE_URL=https://<platform-host>/api/v2
          PROJECT_API_KEY=<单独签发给 AgentMesh 的 ai-client Key>
超时时间: 180 秒（建议；平台仍有自己的更短超时和安全门禁）
```

不要把 Key 写进命令、Skill ZIP、提示词或代码仓库。AgentMesh 页面提供环境变量输入，
**但其密文存储、回显控制与日志脱敏尚未由本仓库验证**；先用测试 Key 验证，再配正式 Key。
数字员工必须能从运行环境访问平台的 `/api/v2` 地址。首次联调先调用 `dba_whoami`，
再读取一个已知实例；检查工具调用审计与平台 Key 使用记录。不要以现有生产 Key 测试写工具。

MCP 首批提供当前告警、实例、业务推断证据、动态只读端点目录与目录内 GET，以及
`metadata_refresh` 操作单的提议、状态、执行、核验。**没有批准工具**：只有平台 `admin`
能批准，AgentMesh 聊天文字不是批准；当前仍需管理员在平台页面确认。执行时平台会重新检查
目标、时效、原申请 Key、负载与并发限制。钉钉会话内确认需要另做带身份核验的回调接入。
工具返回统一为 `{ "data": <平台结果>, "warnings": [...] }`；回答前必须检查 `warnings`，
特别是分页未取全和部分来源不可用，不能将其解释为「没有问题」。

MCP 实现依赖官方 Python SDK `mcp>=2.2,<3`，支持 STDIO；若 AgentMesh 运行时采用旧版 MCP
握手，须实际联调工具发现和调用，不能仅凭配置页判断兼容。MCP SDK 文档：
https://py.sdk.modelcontextprotocol.io/run/

测试：使用 Python 3.10+ 安装 `requirements-dev.txt` 与 `requirements-mcp.txt`，然后运行
`python -m pytest tests -q`；其中包含真实 STDIO 启动、工具发现及环境变量传递的本地假平台测试。

Zabbix:

```env
ZABBIX_BASE_URL=https://zabbix.example.com
ZABBIX_API_TOKEN=replace-with-real-token
ZABBIX_TIMEOUT_SECONDS=8
ZABBIX_VERIFY_TLS=true
```

## Recommended Usage

1. Trigger `dba-skill` for DBA inventory, ownership, unused database, alert evidence, freshness, and diagnostic questions.
2. Use `dba-skill/scripts/dba_api_client.py` when local script execution is available.
3. Use `/dba/directory/options`, `/dba/databases/search`, `/dba/ownership/scope`, `/dba/inventory/summary`, and `/dba/databases/unused` for asset questions.
4. Use `/dba/resolve`, `/dba/alerts/{alert_id}/evidence`, `/dba/context`, `/dba/instances/{id}/freshness`, and diagnostics endpoints for alert analysis.
5. Use `zabbix-readonly` only when host-side CPU, memory, disk, filesystem, load, or I/O evidence is needed.

## Handoff

- Main skill prompt and workflow: `dba-skill/SKILL.md`
- DBA API semantics and pitfalls: `dba-skill/references/dba_api.md`
- **Platform-side API semantics**: `docs/dba-skill-api.md` in the platform repo — it lives
  next to the API it describes, so it changes in the same commit as the API.

> There is deliberately **no separate handoff file.** Two used to exist —
> `dba-skill/HANDOFF.md` here and `docs/external-skill-quickstart.md` in the platform repo —
> and both were copies of this skill's prompt kept somewhere the prompt is not maintained.
> Copies only drift: by the time HANDOFF was removed (2026-09-01) it still claimed to target
> `v2.0.21+` while the platform had shipped 40+ releases, and listed 13 endpoints out of 84.
>
> **The split is: the platform repo documents the API, this repo documents the skill.**
> `SKILL.md` is the single authoritative prompt. If a one-file deliverable is ever needed for
> a third party, generate it from `SKILL.md` rather than hand-maintaining a third copy.

## Tests

```bash
python3 -m pip install -r requirements-dev.txt   # 只有 pytest;客户端本身零依赖
python3 -m pytest tests -q
```

★ **写完用例请确认它在一台干净的机器上跑得起来。** 2026-09-08 这里躺着 15 条刚加的判据用例,
而另一台机器上跑出的是 `No module named pytest` —— 仓库里没有任何东西说该装什么,README 也
一个字没提。**安全网存在不等于安全网工作**:下一个人看到那句报错,大概率就跳过了。

系统自带的 python3 可能是 3.9(macOS)。用例与客户端都兼容 3.9,但 `pytest` 需要自己装;
不想污染系统环境就先建 venv。

## Compatibility Notes

- This repository targets Database AI Center `v2.0.21+` and degrades cleanly on older servers (404/empty) — every capability below is optional, not required:
  - live diagnostic probe drill-down (`probe-catalog` / `probe-run`): `v2.19.0+`
  - knowledge base (`kb-search` / `kb-incidents` / `kb-doc-search`): `v2.32+`, document search `v2.39+`
  - self-describing read-endpoint catalog (`ai-endpoints`): `v2.47.0+`
  - unknown query parameters answered with `422` + `accepted_params` instead of being silently dropped: `v3.33.3+` — **below this, verify a filter actually applied before trusting the rows**
  - fleet-wide metric/health queries and `metric-names`: `v3.38.0+`, vendor metric-name aliasing `v3.39.0+`
  - staleness measured against `last_sync_at` rather than `now`: `v3.41.0+`
  - `database_inventory_coverage` on an instance, and `instances_covered_by_cluster_owner` in inventory coverage: `v3.46.0+`
  - `routing_skip` in `ai/observability`'s audit trail (why a notification was not sent): `v3.47.0+`
- The former `database-ai-center` skill name is retired in favor of `dba-skill`.
- The main path no longer depends on legacy `/alerts -> /alerts/{id}/ai-detail -> /ai/context/{instance_id}`.
- Diagnostics never accepts free-form SQL: use DBA catalog `check_id` values (`diagnostics-run`) or whitelisted probe names + bound params (`probe-run`) only.
- Skill outputs and errors must not expose API keys, database credentials, tokens, encrypted secrets, usernames, or connection strings.
