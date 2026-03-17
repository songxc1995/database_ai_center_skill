---
name: zabbix-readonly
description: Query Zabbix in read-only mode using host IP as the primary identifier, fetch host performance and active problems, and return structured evidence for alert analysis.
---

# Zabbix Readonly

## Overview
Use this skill when the current alert analysis needs host-side performance evidence from Zabbix, such as CPU, load, memory, filesystem usage, or active Zabbix problems.

This skill is read-only.
Do not acknowledge problems, close events, modify hosts, create objects, or execute remote commands.

## Required Config
Read these values from environment variables or runtime config:

- `ZABBIX_BASE_URL`
- `ZABBIX_API_TOKEN`

Optional:

- `ZABBIX_TIMEOUT_SECONDS`
- `ZABBIX_VERIFY_TLS`
- `ZABBIX_HOST_GROUP`

Example:

```env
ZABBIX_BASE_URL=https://zabbix.example.com
ZABBIX_API_TOKEN=replace-with-real-token
ZABBIX_TIMEOUT_SECONDS=8
ZABBIX_VERIFY_TLS=true
```

## Input
Primary target identifier:

- `host_ip`

Optional disambiguation fields:

- `host_name`
- `zabbix_hostid`

Optional:

- `time_range_minutes`
- `requested_metrics`

Defaults:

- `time_range_minutes=60`

## Workflow
1. If `zabbix_hostid` is provided, call `host.get` and use it directly.
2. Else call `hostinterface.get` with `host_ip`.
3. If the IP matches exactly one host, continue.
4. If the IP matches multiple hosts:
   - if `host_name` is provided, call `host.get` to filter candidates by name
   - if exactly one host remains, continue
   - otherwise return candidate hosts and stop
5. Call `problem.get` for current unresolved problems on the resolved host.
6. Call `item.get` for key host metrics.
7. Call `history.get` for recent values within `time_range_minutes`.
8. Return structured facts only.

## API Rules
- Use `POST {ZABBIX_BASE_URL}/api_jsonrpc.php`.
- Use JSON-RPC 2.0.
- Put the token in the request body field `auth`.
- Do not use write operations.

## Preferred Metrics
Prefer these keys or equivalent existing items:

- `system.cpu.util[,idle]`
- `system.cpu.load[all,avg1]`
- `vm.memory.size[pavailable]`
- `vfs.fs.size[/,pused]`
- important filesystem usage items such as `/u01`
- optional network throughput items if already configured

Only use items that already exist in Zabbix.
If a metric is not configured, mark it as unavailable.

## Constraints
- Return facts only.
- Do not invent host events, logs, or topology changes.
- Do not infer a unique host when IP lookup is ambiguous.
- If Zabbix is unavailable, return a failure note instead of fabricated evidence.

## Output Contract
Return this structure:

```json
{
  "target": {
    "hostid": "14494",
    "host": "idc-zp-vm-fosun-fssc-slnc-prd-oracle-02",
    "name": "idc-zp-vm-fosun-fssc-slnc-prd-oracle-02",
    "ip": "10.99.127.61"
  },
  "problems": [],
  "metrics": {
    "cpu_idle_pct": 97.73,
    "load_avg_1m": 0.08,
    "memory_available_pct": 28.40,
    "filesystems": [
      {
        "mount": "/u01",
        "used_pct": 61.29
      }
    ]
  },
  "history_summary": [
    "过去60分钟 CPU idle 维持高位",
    "过去60分钟 /u01 使用率基本稳定"
  ],
  "notes": []
}
```

## Failure Handling
- If `host_ip` matches no hosts, return `notes` with `host not found in zabbix`.
- If `host_ip` matches multiple hosts and cannot be disambiguated, return:
  - `ambiguous host ip`
  - candidate host list
- Do not guess the final host.
- If some metrics are missing, keep partial results and note missing items.
- If `problem.get` or `history.get` fails, keep available facts and record the failure in `notes`.
