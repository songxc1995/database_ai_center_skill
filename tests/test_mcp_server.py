"""The optional MCP adapter is tested with the same fake HTTP style as the DBA CLI."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


pytest.importorskip("mcp")
from mcp import Client, StdioServerParameters  # noqa: E402


SERVER_FILE = Path(__file__).resolve().parents[1] / "dba-skill" / "mcp_server.py"
spec = importlib.util.spec_from_file_location("dba_mcp_server", SERVER_FILE)
assert spec and spec.loader
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FakePlatform(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, str]] = []
    catalog: list[dict] = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def _reply(self):
        self.requests.append((self.command, self.path, self.headers.get("X-API-Key", "")))
        if self.path == "/api/v2/ai-endpoints":
            body = self.catalog
        elif self.path.startswith("/api/v2/dba/actions"):
            body = {"id": 7, "status": "pending"}
        else:
            body = {"ok": True, "path": self.path}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def platform(monkeypatch):
    FakePlatform.requests = []
    FakePlatform.catalog = [
        {"path": "/api/v2/instances/{instance_id}", "methods": ["GET"]},
        {"path": "/api/v2/dba/actions/{order_id}/execute", "methods": ["POST"]},
    ]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakePlatform)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("PROJECT_API_BASE_URL", f"http://127.0.0.1:{httpd.server_port}/api/v2")
    monkeypatch.setenv("PROJECT_API_KEY", "mcp-test-secret")
    yield
    httpd.shutdown()
    httpd.server_close()
    worker.join(timeout=2)


def test_read_endpoint_checks_live_get_catalog_and_passes_key(platform):
    payload = server.dba_read_endpoint("/api/v2/instances/42", {"include_components": "true"})

    assert payload["data"]["ok"] is True
    assert payload["warnings"] == []
    assert FakePlatform.requests == [
        ("GET", "/api/v2/ai-endpoints", "mcp-test-secret"),
        ("GET", "/api/v2/instances/42?include_components=true", "mcp-test-secret"),
    ]


def test_read_endpoint_rejects_non_catalog_or_write_paths(platform):
    with pytest.raises(ValueError, match="不在当前"):
        server.dba_read_endpoint("/api/v2/dba/actions/7/execute")
    assert len(FakePlatform.requests) == 1
    with pytest.raises(ValueError, match="path 必须"):
        server.dba_read_endpoint("https://evil.example/api/v2/instances/42")
    assert len(FakePlatform.requests) == 1


def test_proposal_never_approves_or_executes(platform):
    payload = server.dba_propose_metadata_refresh(12, "需要更新业务表结构证据", ["alert:1"])

    assert payload["data"]["status"] == "pending"
    assert FakePlatform.requests == [("POST", "/api/v2/dba/actions", "mcp-test-secret")]


def test_missing_config_fails_without_network(monkeypatch):
    monkeypatch.delenv("PROJECT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="PROJECT_API_KEY"):
        server.dba_whoami()


def test_client_error_does_not_expose_key(monkeypatch):
    monkeypatch.setenv("PROJECT_API_BASE_URL", "http://example.invalid/api/v2")
    monkeypatch.setenv("PROJECT_API_KEY", "mcp-test-secret")
    fake = subprocess.CompletedProcess([], 1, "", "failed with mcp-test-secret")
    monkeypatch.setattr(server.subprocess, "run", lambda *_a, **_kw: fake)
    with pytest.raises(RuntimeError, match=r"\[redacted\]") as exc:
        server.dba_whoami()
    assert "mcp-test-secret" not in str(exc.value)


def test_client_warnings_are_returned_to_model(monkeypatch):
    monkeypatch.setenv("PROJECT_API_BASE_URL", "http://example.invalid/api/v2")
    monkeypatch.setenv("PROJECT_API_KEY", "mcp-test-secret")
    fake = subprocess.CompletedProcess(
        [], 0, '{"items":[]}', '{"warning":"partial_page","key":"mcp-test-secret"}\n',
    )
    monkeypatch.setattr(server.subprocess, "run", lambda *_a, **_kw: fake)
    result = server.dba_alerts()
    assert result == {
        "data": {"items": []},
        "warnings": [{"warning": "partial_page", "key": "[redacted]"}],
    }


@pytest.mark.anyio
async def test_mcp_lists_and_calls_tool(platform):
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"dba_whoami", "dba_read_endpoint", "dba_propose_metadata_refresh"} <= names
        assert "approve" not in " ".join(names)
        result = await client.call_tool("dba_action_order_status", {"order_id": 7})
        assert result.is_error is False
        assert "pending" in str(result)


@pytest.mark.anyio
async def test_real_stdio_transport_and_env_injection(platform):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)],
        env=dict(os.environ),
    )
    async with Client(params) as client:
        listed = await client.list_tools()
        assert any(tool.name == "dba_whoami" for tool in listed.tools)
        result = await client.call_tool("dba_action_order_status", {"order_id": 7})
        assert result.is_error is False
        assert "pending" in str(result)
    assert FakePlatform.requests == [("GET", "/api/v2/dba/actions/7", "mcp-test-secret")]


def test_stdio_startup_with_missing_key_does_not_print_secret_to_stdout(tmp_path):
    env = {**os.environ, "PROJECT_API_KEY": "mcp-test-secret"}
    process = subprocess.Popen(
        [sys.executable, str(SERVER_FILE)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = process.communicate(timeout=10)
    assert out == ""
    assert "mcp-test-secret" not in err
