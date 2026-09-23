"""AgentMesh STDIO tools for the existing DBA client.

The model never receives the platform API key. AgentMesh passes it to this process as
PROJECT_API_KEY; the client sends it to Database AI Center as X-API-Key. All database
access, authorization, action approval and load gates remain in the platform.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from mcp.server import MCPServer


mcp = MCPServer("database-ai-center")
CLIENT = Path(__file__).resolve().parent / "scripts" / "dba_api_client.py"
MAX_RESULT_BYTES = 2_000_000
CLIENT_TIMEOUT_SECONDS = 120
_PATH_PARAM = re.compile(r"^\{[^{}]+\}$")


def _run(*args: str) -> Any:
    """Cross the existing, redacting DBA client interface without leaking stdout to MCP."""
    if not os.environ.get("PROJECT_API_BASE_URL") or not os.environ.get("PROJECT_API_KEY"):
        raise ValueError("MCP 进程缺少 PROJECT_API_BASE_URL 或 PROJECT_API_KEY")
    try:
        result = subprocess.run(
            [sys.executable, str(CLIENT), *args],
            capture_output=True,
            text=True,
            timeout=CLIENT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("DBA 平台查询超时；不要把超时解释为健康或无数据") from exc
    if result.returncode:
        # The client redacts known secrets; redact the configured key once more at this seam.
        detail = (result.stderr or result.stdout or "DBA client failed")[:2000]
        detail = detail.replace(os.environ["PROJECT_API_KEY"], "[redacted]")
        raise RuntimeError(detail)
    if len(result.stdout.encode("utf-8")) > MAX_RESULT_BYTES:
        raise RuntimeError("DBA 返回超过 2 MB；请缩小查询范围，不能静默截断结果")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DBA 客户端返回了无效 JSON") from exc
    # The client writes important caveats (partial pages, degraded fan-out, retry) to stderr.
    # Dropping them would turn incomplete evidence into a confident-looking answer.
    warnings: list[Any] = []
    for line in result.stderr.splitlines():
        safe = line.replace(os.environ["PROJECT_API_KEY"], "[redacted]")
        try:
            warnings.append(json.loads(safe))
        except json.JSONDecodeError:
            warnings.append(safe)
    return {"data": data, "warnings": warnings}


def _positive(value: int, name: str) -> int:
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _read_catalog() -> dict[str, Any]:
    response = _run("ai-endpoints")
    catalog = response["data"]
    if not isinstance(catalog, list):
        raise RuntimeError("平台没有返回有效的只读端点目录")
    return {
        "data": [row for row in catalog if isinstance(row, dict) and "GET" in row.get("methods", [])],
        "warnings": response["warnings"],
    }


def _normalize_path(path: str) -> str:
    path = path.strip()
    if not path.startswith("/api/v2/") or "?" in path or "#" in path or "//" in path:
        raise ValueError("path 必须是目录中的 /api/v2/... 路径；参数请单独传入")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("path 不得包含相对路径段")
    return path


def _matches_catalog_path(actual: str, template: str) -> bool:
    given = actual.strip("/").split("/")
    expected = template.strip("/").split("/")
    if len(given) != len(expected):
        return False
    return all(_PATH_PARAM.fullmatch(t) and bool(g) or g == t for g, t in zip(given, expected))


@mcp.tool()
def dba_whoami() -> Any:
    """检查数据库平台身份、Key 有效期、限流及健康状态；连接异常时先调用。"""
    return _run("whoami")


@mcp.tool()
def dba_alerts(status: str = "active", severity: str = "", instance_id: int = 0, limit: int = 200) -> Any:
    """查询当前或历史告警；返回实例和告警证据，不要把空结果当作采集健康。"""
    if status not in {"active", "resolved", "all"}:
        raise ValueError("status 必须是 active、resolved 或 all")
    if severity and severity not in {"low", "medium", "high", "critical"}:
        raise ValueError("severity 值无效")
    args = ["alerts", "--status", status, "--limit", str(_positive(limit, "limit"))]
    if severity:
        args += ["--severity", severity]
    if instance_id:
        args += ["--instance-id", str(_positive(instance_id, "instance_id"))]
    return _run(*args)


@mcp.tool()
def dba_instance(instance_id: int = 0, ip: str = "") -> Any:
    """按实例 ID 或 IP 获取实例详情、数据新鲜度、备份和活动告警。"""
    if bool(instance_id) == bool(ip.strip()):
        raise ValueError("instance_id 和 ip 必须且只能提供一个")
    if instance_id:
        return _run("instance", "--instance-id", str(_positive(instance_id, "instance_id")))
    return _run("instance", "--ip", ip.strip())


@mcp.tool()
def dba_business_evidence(database_id: int) -> Any:
    """读取已持久化的库对象名与注释，供模型推断业务；不连接源库。"""
    return _run("business-inference-evidence", "--database-id", str(_positive(database_id, "database_id")))


@mcp.tool()
def dba_read_endpoints() -> Any:
    """列出当前 ai-client 可访问的 GET 端点；长尾查询先发现再读取。"""
    return _read_catalog()


@mcp.tool()
def dba_read_endpoint(path: str, params: dict[str, str] | None = None) -> Any:
    """只读取 live 目录中的 GET 路径；不接受任意 URL、SQL 或写入请求。"""
    path = _normalize_path(path)
    catalog = _read_catalog()
    if not any(_matches_catalog_path(path, str(row.get("path", ""))) for row in catalog["data"]):
        raise ValueError("path 不在当前 ai-client GET 目录中")
    args = ["get", path]
    for name, value in (params or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("params 必须是普通字符串键值对")
        args += ["--param", f"{name}={value}"]
    result = _run(*args)
    result["warnings"] = catalog["warnings"] + result["warnings"]
    return result


@mcp.tool()
def dba_propose_metadata_refresh(database_id: int, reason: str, evidence_refs: list[str] | None = None) -> Any:
    """提出单库元数据刷新操作单；仅提议，不批准、不执行，须独立 admin 审批。"""
    if not 5 <= len(reason.strip()) <= 500:
        raise ValueError("reason 长度必须为 5–500 字符")
    refs = evidence_refs or []
    if len(refs) > 10 or any(not 1 <= len(ref.strip()) <= 300 for ref in refs):
        raise ValueError("evidence_refs 最多 10 条，每条 1–300 字符")
    args = ["propose-metadata-refresh", "--database-id", str(_positive(database_id, "database_id")), "--reason", reason.strip()]
    for ref in refs:
        args += ["--evidence-ref", ref.strip()]
    return _run(*args)


@mcp.tool()
def dba_action_order_status(order_id: int) -> Any:
    """查看操作单状态和批准人；只有状态 approved 才能尝试执行。"""
    return _run("action-order-status", "--order-id", str(_positive(order_id, "order_id")))


@mcp.tool()
def dba_execute_action_order(order_id: int) -> Any:
    """执行原申请 Key 创建且独立 admin 已批准的单次元数据刷新操作单。聊天文字不是批准。"""
    return _run("execute-action-order", "--order-id", str(_positive(order_id, "order_id")))


@mcp.tool()
def dba_verify_action_order(order_id: int) -> Any:
    """核验已完成的刷新任务并将结果写回操作单；失败或跳过不得称为成功。"""
    return _run("verify-action-order", "--order-id", str(_positive(order_id, "order_id")))


if __name__ == "__main__":
    # stdout belongs exclusively to MCP JSON-RPC; never print diagnostics here.
    mcp.run(transport="stdio")
