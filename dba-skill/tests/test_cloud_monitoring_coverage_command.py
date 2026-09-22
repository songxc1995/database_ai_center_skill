from __future__ import annotations

import importlib.util
from pathlib import Path


def _client_module():
    path = Path(__file__).parents[1] / "scripts" / "dba_api_client.py"
    spec = importlib.util.spec_from_file_location("dba_api_client_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_monitoring_coverage_command_requests_aliyun_missing_rows(monkeypatch) -> None:
    client = _client_module()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_request(method: str, path: str, *, params=None, body=None):
        calls.append((method, path, params or {}))
        return {"items": [], "total": 0, "truncated": False}

    monkeypatch.setattr(client, "_request", fake_request)
    args = client.build_parser().parse_args([
        "cloud-monitoring-coverage",
        "--vendor", "aliyun",
        "--missing-only",
        "--limit", "100",
        "--all",
    ])

    assert args.all is True
    assert args.func(args) == {"items": [], "total": 0, "truncated": False}
    assert calls == [(
        "GET",
        "/dba/cloud-rds/monitoring-coverage",
        {
            "vendor": "aliyun",
            "coverage": "missing",
            "tenant_id": None,
            "limit": 100,
            "offset": None,
        },
    )]
