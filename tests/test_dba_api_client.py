import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "dba-skill" / "scripts" / "dba_api_client.py"


class RecordingHandler(BaseHTTPRequestHandler):
    requests = []
    responses = {}

    def do_GET(self):
        self._record()
        status, payload = self.responses.get(self.path, (200, {"ok": True}))
        self._send(status, payload)

    def do_POST(self):
        self._record()
        status, payload = self.responses.get(self.path, (200, {"ok": True}))
        self._send(status, payload)

    def log_message(self, format, *args):
        return

    def _record(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        parsed = urlparse(self.path)
        self.requests.append(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "headers": dict(self.headers),
                "body": body.decode("utf-8") if body else "",
            }
        )

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DbaApiClientTest(unittest.TestCase):
    def setUp(self):
        RecordingHandler.requests = []
        RecordingHandler.responses = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.workdir = tempfile.TemporaryDirectory()
        self.env = {
            **os.environ,
            "PROJECT_API_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/api/v2",
            "PROJECT_API_KEY": "super-secret-key",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.workdir.cleanup()

    def run_client(self, *args, env=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLIENT), *args],
            env=self.env if env is None else env,
            cwd=self.workdir.name if cwd is None else cwd,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_alerts_list_defaults_to_active_status(self):
        result = self.run_client("alerts-list")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/alerts")
        self.assertEqual(request["query"]["status"], ["active"])
        self.assertEqual(request["query"]["page"], ["1"])
        self.assertEqual(request["query"]["page_size"], ["20"])

    def test_alerts_list_can_omit_status_filter(self):
        result = self.run_client("alerts-list", "--all-statuses", "--severity", "critical", "--page-size", "5")

        self.assertEqual(result.returncode, 0, result.stderr)
        query = RecordingHandler.requests[0]["query"]
        self.assertNotIn("status", query)
        self.assertEqual(query["severity"], ["critical"])
        self.assertEqual(query["page_size"], ["5"])

    def test_alerts_list_rejects_non_positive_page_before_http_request(self):
        result = self.run_client("alerts-list", "--page", "0")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(RecordingHandler.requests, [])
        self.assertIn("must be greater than or equal to 1", result.stderr)

    def test_directory_options_sends_api_key_and_query_parameters(self):
        result = self.run_client(
            "directory-options",
            "--type",
            "application",
            "--search",
            "Pay",
            "--include-inactive",
            "--limit",
            "5",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        # 服务端说的原样保留……
        self.assertEqual(body["ok"], True)
        # ……外加**生效的查询**回显。没有它,`--type nosuch` 返回的空列表和"确实没有"
        # 一个字都不差:打错一个词表值,换来的是一个理直气壮的 0。
        self.assertEqual(body["applied_filters"]["type"], "application")
        self.assertEqual(body["applied_filters"]["search"], "Pay")
        self.assertEqual(body["applied_filters"]["limit"], 5)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/dba/directory/options")
        headers = {key.lower(): value for key, value in request["headers"].items()}
        self.assertEqual(headers["x-api-key"], "super-secret-key")
        self.assertEqual(request["query"]["type"], ["application"])
        self.assertEqual(request["query"]["search"], ["Pay"])
        self.assertEqual(request["query"]["include_inactive"], ["true"])
        self.assertEqual(request["query"]["limit"], ["5"])

    def test_classification_sends_engine_and_topology_filters(self):
        result = self.run_client("classification", "--type", "oracle", "--topology", "dataguard")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/instances/classification")
        self.assertEqual(request["query"]["type"], ["oracle"])
        self.assertEqual(request["query"]["topology"], ["dataguard"])

    def test_classification_rejects_unknown_engine_before_http_request(self):
        result = self.run_client("classification", "--type", "mariadb")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(RecordingHandler.requests, [])

    def test_env_file_fallback_loads_project_config_without_printing_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text(
                f"PROJECT_API_BASE_URL=http://127.0.0.1:{self.server.server_port}/api/v2\n"
                "PROJECT_API_KEY=dotenv-secret-key\n"
                "IGNORED_SECRET=should-not-load\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            for key in (
                "PROJECT_API_BASE_URL",
                "PROJECT_API_KEY",
                "PROJECT_TIMEOUT_SECONDS",
                "PROJECT_STALE_AFTER_HOURS",
            ):
                env.pop(key, None)

            result = self.run_client("inventory-summary", env=env, cwd=temp_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        headers = {key.lower(): value for key, value in RecordingHandler.requests[0]["headers"].items()}
        self.assertEqual(headers["x-api-key"], "dotenv-secret-key")
        self.assertNotIn("dotenv-secret-key", result.stdout)
        self.assertNotIn("dotenv-secret-key", result.stderr)

    def test_nearest_env_file_overrides_stale_process_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text(
                f"PROJECT_API_BASE_URL=http://127.0.0.1:{self.server.server_port}/api/v2\n"
                "PROJECT_API_KEY=fresh-dotenv-key\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "PROJECT_API_BASE_URL": "http://127.0.0.1:1/api/v2",
                "PROJECT_API_KEY": "stale-process-key",
            }

            result = self.run_client("inventory-summary", env=env, cwd=temp_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        headers = {key.lower(): value for key, value in RecordingHandler.requests[0]["headers"].items()}
        self.assertEqual(headers["x-api-key"], "fresh-dotenv-key")
        self.assertNotIn("stale-process-key", result.stderr)

    def test_database_search_preserves_ownership_filters(self):
        result = self.run_client(
            "databases-search",
            "--business",
            "Payments",
            "--contact",
            "Alice",
            "--contact-role",
            "technical",
            "--include-inactive",
            "--include-system-dbs",
            "--is-in-use",
            "false",
            "--limit",
            "25",
            "--offset",
            "50",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        query = RecordingHandler.requests[0]["query"]
        self.assertEqual(query["business"], ["Payments"])
        self.assertEqual(query["contact"], ["Alice"])
        self.assertEqual(query["contact_role"], ["technical"])
        self.assertEqual(query["include_inactive"], ["true"])
        self.assertEqual(query["include_system_dbs"], ["true"])
        self.assertEqual(query["is_in_use"], ["false"])
        self.assertEqual(query["limit"], ["25"])
        self.assertEqual(query["offset"], ["50"])


    def test_user_config_file_answers_when_there_is_no_env_and_no_cwd_to_search(self):
        """Finder/Explorer 启动的宿主:cwd 是 / 或 app bundle,向上找不到任何 .env,而 GUI
        进程几乎继承不到 shell 里 export 的东西 —— 这时唯一的凭据来源就是这个文件。
        """
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as work_dir:
            config = Path(home_dir, ".dba-skill", "config")
            config.parent.mkdir(parents=True)
            config.write_text(
                f"PROJECT_API_BASE_URL=http://127.0.0.1:{self.server.server_port}/api/v2\n"
                "PROJECT_API_KEY=user-config-key\n",
                encoding="utf-8",
            )
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PROJECT_API_KEY", "PROJECT_API_BASE_URL")}
            env["HOME"] = home_dir
            env["USERPROFILE"] = home_dir  # Windows 上 Path.home() 读这个

            result = self.run_client("inventory-summary", env=env, cwd=work_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        headers = {k.lower(): v for k, v in RecordingHandler.requests[-1]["headers"].items()}
        self.assertEqual(headers["x-api-key"], "user-config-key")

    def test_user_config_never_shadows_a_key_that_is_already_configured(self):
        """配置文件在最底层:它只补空缺,不覆盖已经配好的东西。

        否则给 Claude Code 用户加一个配置文件,就会悄悄改掉他 settings.json 里那把 key ——
        而两个来源打架、其中一个静默获胜,正是本仓库两个方向都栽过的那件事。
        """
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as work_dir:
            config = Path(home_dir, ".dba-skill", "config")
            config.parent.mkdir(parents=True)
            config.write_text("PROJECT_API_KEY=user-config-key\n", encoding="utf-8")
            env = {
                **os.environ,
                "HOME": home_dir,
                "USERPROFILE": home_dir,
                "PROJECT_API_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/api/v2",
                "PROJECT_API_KEY": "already-configured-key",
            }

            result = self.run_client("inventory-summary", env=env, cwd=work_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        headers = {k.lower(): v for k, v in RecordingHandler.requests[-1]["headers"].items()}
        self.assertEqual(headers["x-api-key"], "already-configured-key")
        self.assertNotIn("user-config-key", result.stderr)
    def test_diagnostics_run_posts_only_allowlisted_checks(self):
        result = self.run_client(
            "diagnostics-run",
            "--instance-id",
            "12",
            "--checks",
            "database_sizes,storage",
            "--database-name",
            "payments",
            "--timeout-seconds",
            "20",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/v2/dba/instances/12/diagnostics/run")
        body = json.loads(request["body"])
        self.assertEqual(body["checks"], ["database_sizes", "storage"])
        self.assertEqual(body["database_name"], "payments")
        self.assertEqual(body["timeout_seconds"], 20)
        self.assertNotIn("sql", body)

    def test_rejects_free_form_sql_before_http_request(self):
        result = self.run_client(
            "diagnostics-run",
            "--instance-id",
            "12",
            "--checks",
            "database_sizes",
            "--sql",
            "select * from users",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(RecordingHandler.requests, [])
        error = json.loads(result.stderr)
        self.assertEqual(error["error"], "free_form_sql_not_supported")
        self.assertNotIn("super-secret-key", result.stderr)

    def test_http_errors_are_structured_and_token_is_redacted(self):
        RecordingHandler.responses["/api/v2/dba/inventory/summary"] = (
            403,
            {"message": "denied for super-secret-key"},
        )

        result = self.run_client("inventory-summary")

        self.assertNotEqual(result.returncode, 0)
        error = json.loads(result.stderr)
        self.assertEqual(error["error"], "http_error")
        self.assertEqual(error["status_code"], 403)
        self.assertIn("<redacted>", error["response"])
        self.assertNotIn("super-secret-key", result.stderr)

    def test_probe_catalog_gets_instance_probe_catalog(self):
        result = self.run_client("probe-catalog", "--instance-id", "12")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/instances/12/diagnostics/catalog")

    def test_probe_run_posts_probe_name_and_bound_params(self):
        result = self.run_client(
            "probe-run",
            "--instance-id",
            "12",
            "--probe",
            "sql_plan",
            "--sql-id",
            "gm9ttamf39c40",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/v2/instances/12/diagnostics/probe")
        body = json.loads(request["body"])
        self.assertEqual(body["probe"], "sql_plan")
        self.assertEqual(body["params"], {"sql_id": "gm9ttamf39c40"})

    def test_probe_run_rejects_free_form_sql_before_http_request(self):
        result = self.run_client(
            "probe-run",
            "--instance-id",
            "12",
            "--probe",
            "sql_plan",
            "--sql",
            "select * from users",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(RecordingHandler.requests, [])
        error = json.loads(result.stderr)
        self.assertEqual(error["error"], "free_form_sql_not_supported")

    def test_kb_search_sends_semantic_and_filter_params(self):
        result = self.run_client(
            "kb-search",
            "--q",
            "connections spike",
            "--db-type",
            "oracle",
            "--sort",
            "recency",
            "--limit",
            "10",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/knowledge/entries")
        self.assertEqual(request["query"]["q"], ["connections spike"])
        self.assertEqual(request["query"]["db_type"], ["oracle"])
        self.assertEqual(request["query"]["sort"], ["recency"])
        self.assertEqual(request["query"]["limit"], ["10"])

    def test_kb_incidents_url_encodes_root_cause_key(self):
        result = self.run_client(
            "kb-incidents",
            "--root-cause-key",
            "pool exhausted/oracle",
            "--limit",
            "5",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        # space + slash in the key are percent-encoded so it stays one path segment
        self.assertEqual(
            request["path"],
            "/api/v2/knowledge/entries/pool%20exhausted%2Foracle/incidents",
        )
        self.assertEqual(request["query"]["limit"], ["5"])

    def test_kb_doc_search_requires_query(self):
        missing = self.run_client("kb-doc-search")
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(RecordingHandler.requests, [])

        ok = self.run_client("kb-doc-search", "--q", "failover runbook", "--limit", "3")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/knowledge/documents/search")
        self.assertEqual(request["query"]["q"], ["failover runbook"])
        self.assertEqual(request["query"]["limit"], ["3"])

    def test_ai_endpoints_lists_the_catalog(self):
        result = self.run_client("ai-endpoints")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/ai-endpoints")


    # ── v3.32+ 策展命令 ─────────────────────────────────────────────
    # 这四个命令是手工连生产验证过的,但手工验证挡不住回归:改错一个路径或参数名,
    # 在没有测试的情况下不会有任何东西变红。今天平台侧已经三次出现「手工验证过仍然错」。

    def test_alerts_uses_the_flat_dba_list_not_the_paginated_ui_endpoint(self):
        result = self.run_client("alerts")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/dba/alerts")
        # 默认只问当前在响的 —— 问「现在有哪些告警」几乎不会是想要历史全量
        self.assertEqual(request["query"]["status"], ["active"])

    def test_backups_coverage_defaults_to_at_risk(self):
        """问「哪些库没有有效备份」九成是想知道什么坏了;默认给全量 188 台等于把筛选推回调用方。"""
        result = self.run_client("backups-coverage")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/dba/backups/coverage")
        self.assertEqual(request["query"]["verdict"], ["at_risk"])

    def test_backups_coverage_all_drops_the_verdict_filter(self):
        result = self.run_client("backups-coverage", "--verdict", "all")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertNotIn("verdict", request["query"])

    def test_capacity_forecast_can_ask_for_the_gaps(self):
        """测不出的实例必须能显式看到 —— 从列表里消失会被读成「这台没有容量风险」。"""
        result = self.run_client("capacity-forecast", "--include-gaps", "--max-days", "90")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/dba/capacity/forecast")
        self.assertEqual(request["query"]["include_gaps"], ["true"])
        self.assertEqual(request["query"]["max_days"], ["90.0"])

    def test_capacity_forecast_omits_include_gaps_when_not_asked(self):
        result = self.run_client("capacity-forecast")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("include_gaps", RecordingHandler.requests[0]["query"])

    def test_silence_report_targets_one_instance(self):
        result = self.run_client("silence-report", "--instance-id", "3")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/dba/instances/3/silence-report")

    def test_silence_report_requires_an_instance(self):
        """漏传实例时必须报错,而不是悄悄查了别的东西。"""
        result = self.run_client("silence-report")
        self.assertNotEqual(result.returncode, 0)

    def test_get_fetches_arbitrary_read_path_with_params(self):
        result = self.run_client(
            "get", "/dashboard/trends", "--param", "hours=6", "--param", "bucket_minutes=15"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v2/dashboard/trends")
        self.assertEqual(request["query"]["hours"], ["6"])
        self.assertEqual(request["query"]["bucket_minutes"], ["15"])

    def test_get_strips_catalog_full_path_prefix(self):
        # the ai-endpoints catalog returns full paths like /api/v2/topology;
        # `get` must not double the /api/v2 prefix.
        result = self.run_client("get", "/api/v2/topology")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["path"], "/api/v2/topology")

    # ── ELK log evidence ──
    def test_elk_status_hits_the_status_endpoint(self):
        result = self.run_client("elk-status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["path"], "/api/v2/elk/status")

    def test_elk_coverage_hits_the_coverage_endpoint(self):
        result = self.run_client("elk-coverage")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["path"], "/api/v2/elk/coverage")

    def test_elk_search_sends_host_time_and_level_filters(self):
        result = self.run_client(
            "elk-search", "--host-ip", "10.101.240.83", "--levels", "ERROR,FATAL",
            "--start", "2026-07-30T00:00:00Z", "--size", "50",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/elk/search")
        self.assertEqual(request["query"]["host_ip"], ["10.101.240.83"])
        self.assertEqual(request["query"]["levels"], ["ERROR,FATAL"])
        self.assertEqual(request["query"]["start"], ["2026-07-30T00:00:00Z"])
        self.assertEqual(request["query"]["size"], ["50"])

    # ── cloud data ──
    def test_cloud_rightsizing_sends_window_and_vendor(self):
        result = self.run_client("cloud-rightsizing", "--window-days", "30", "--vendor", "huawei")
        self.assertEqual(result.returncode, 0, result.stderr)
        request = RecordingHandler.requests[0]
        self.assertEqual(request["path"], "/api/v2/cloud-rds/rightsizing")
        self.assertEqual(request["query"]["window_days"], ["30"])
        self.assertEqual(request["query"]["vendor"], ["huawei"])

    def test_cloud_cost_history_hits_the_endpoint(self):
        result = self.run_client("cloud-cost-history")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["path"], "/api/v2/cloud-rds/cost-history")

    # ── backup determination ──
    def test_backups_hits_the_instance_backups_endpoint(self):
        RecordingHandler.responses = {
            "/api/v2/instances/12/backups": (
                200, {"instance_id": 12, "backup_method": "expdp", "determination": "declared_no_evidence"},
            )
        }
        result = self.run_client("backups", "--instance-id", "12")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["path"], "/api/v2/instances/12/backups")
        # refresh defaults off → not sent (None is dropped by _clean_params)
        self.assertNotIn("refresh", RecordingHandler.requests[0]["query"])
        self.assertEqual(json.loads(result.stdout)["determination"], "declared_no_evidence")

    def test_backups_refresh_flag_is_forwarded(self):
        result = self.run_client("backups", "--instance-id", "7", "--refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RecordingHandler.requests[0]["query"]["refresh"], ["true"])


if __name__ == "__main__":
    unittest.main()


# --- Field-report fixes (2026-09-02) ------------------------------------------------------
#
# The suite above drives the client as a subprocess against a fake server, which is right for
# end-to-end behaviour. These load it as a module to test the helpers directly.
import importlib.util as _importlib_util

_spec = _importlib_util.spec_from_file_location("dba_api_client", CLIENT)
client_module = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(client_module)


def test_envelope_handles_all_three_platform_shapes():
    """There is no single envelope, and guessing wrong is how a caller gets 'str' has no 'get'."""
    _envelope = client_module._envelope

    items, meta = _envelope([1, 2, 3])
    assert items == [1, 2, 3] and meta["shape"] == "list"

    items, meta = _envelope({"items": [1], "total": 9, "limit": 1, "offset": 0, "truncated": True})
    assert items == [1] and meta["total"] == 9 and meta["truncated"] is True

    items, meta = _envelope({"items": [1], "total": 9, "page": 1, "page_size": 1, "has_next": True})
    assert items == [1] and meta["page_size"] == 1

    # A single object is not a collection and must not be treated as one.
    assert _envelope({"instance_id": 7, "determination": "verified"}) == (None, {})


def test_truncated_page_warns_on_stderr(capsys):
    """A partial page is shaped exactly like a complete one.

    Production 2026-09-01: a caller asked which instances had no databases, got 2000 of 2072
    rows, and the two it was looking for were among the missing 72.
    """
    import json as _json

    _warn_if_truncated = client_module._warn_if_truncated

    _warn_if_truncated({"items": [1, 2], "total": 2072, "truncated": True}, "/instances")
    err = capsys.readouterr().err
    payload = _json.loads(err)
    assert payload["warning"] == "partial_result"
    assert payload["total"] == 2072 and payload["returned"] == 2


def test_a_complete_page_is_silent(capsys):
    _warn_if_truncated = client_module._warn_if_truncated

    _warn_if_truncated({"items": [1, 2], "total": 2, "truncated": False}, "/instances")
    assert capsys.readouterr().err == ""


def test_auth_failure_reports_where_the_credential_came_from():
    """A stale inherited key and a revoked key both return 401.

    Running from the wrong directory produced AUTH_INVALID_API_KEY, which reads as "the key
    was revoked" when it means "there is no .env here". Refusing to run would break the
    documented setup (the runtime supplies the key through the environment), so the fix is
    provenance, not refusal.
    """
    _credential_provenance = client_module._credential_provenance

    prov = _credential_provenance()
    assert "credential_source" in prov
    assert "cwd" in prov
    assert isinstance(prov["env_files_searched"], list)


def test_projection_keeps_only_requested_fields():
    _project = client_module._project

    payload = {"items": [{"id": 1, "host": "a", "junk": "x"}], "total": 1}
    out = _project(payload, ["id", "host"])
    assert out["items"] == [{"id": 1, "host": "a"}]
    assert out["total"] == 1, "envelope metadata must survive projection"


def test_onboarding_check_separates_unreadable_from_uncovered(monkeypatch):
    """★ The bug this test exists for was written twice in one day.

    /elk/coverage puts its rows under `instances`, not `items`, each with its own `covered`
    flag. Reading it as `items` produced an empty set and reported every instance as "not
    shipping logs" — a wrong-field lookup and a real gap return the same empty answer. So an
    unreadable check must report `ok: None`, never `ok: False`.
    """
    import argparse

    client = client_module

    def fake_get(path, params=None):
        if path == "/elk/coverage":
            return {"configured": True, "instances": [{"id": 5, "covered": True}]}
        if path == "/instances/5":
            return {"id": 5, "host": "10.0.0.5", "contact_person": "someone",
                    "database_inventory_coverage": "owns"}
        if path == "/databases":
            return {"items": [{"id": 1}], "total": 1}
        if path == "/instances/5/backups":
            return {"backup_method": "rman"}
        if path == "/dba/backups/coverage":
            # The real endpoint always answers with an envelope. A dict carrying no
            # collection key now reads as "unreadable", so the fake has to have the shape
            # the platform actually sends — otherwise it pins a response nothing returns.
            return {"items": [{"instance_id": 5, "verdict": "ok"}], "counts": {}}
        return {}

    monkeypatch.setattr(client, "_try_get", fake_get)
    out = client.cmd_onboarding_check(argparse.Namespace(instance_id=5))
    assert out["checks"]["elk_logs"]["ok"] is True
    assert out["ok"] is True

    def broken_elk(path, params=None):
        return {"unavailable": True} if path == "/elk/coverage" else fake_get(path, params)

    # A second invocation in the same process is a second run: the fleet-wide cache that
    # makes a fan-out cheap must not answer this scenario with the previous one's data.
    client._SHARED_CACHE.clear()
    monkeypatch.setattr(client, "_try_get", broken_elk)
    out = client.cmd_onboarding_check(argparse.Namespace(instance_id=5))
    assert out["checks"]["elk_logs"]["ok"] is None, "unreadable must not read as uncovered"
    assert out["unknown"] == ["elk_logs"]
    assert out["ok"] is False, "an unanswerable check is not a clean bill of health"


def test_global_flags_work_before_and_after_the_subcommand():
    """argparse applies subparser defaults last, so a plain default silently ate `--all`."""
    build_parser = client_module.build_parser

    parser = build_parser()
    assert parser.parse_args(["--all", "get", "/instances"]).all is True
    assert parser.parse_args(["get", "/instances", "--all"]).all is True
    assert parser.parse_args(["get", "/instances"]).all is False


def test_a_closed_pipe_is_not_reported_as_a_failure():
    """`| head` is the normal way to look at these outputs, and it closes the pipe.

    Python raises again while flushing stdout at shutdown, printing a traceback that reads
    like the command failed — the rows already written were correct and the truncation was
    the caller's own choice. Reported from the field 2026-09-02.
    """
    import subprocess as _sp

    script = (
        "import sys, runpy;"
        f"sys.argv=['x','--help'];"
        "print('x' * 100000)"
    )
    # Drive the real entry point: a huge stdout write into a reader that exits immediately.
    proc = _sp.Popen(
        [sys.executable, "-c",
         "import sys;"
         "sys.path.insert(0, %r);" % str(CLIENT.parent) +
         "import importlib.util as u;"
         "s=u.spec_from_file_location('c', %r);" % str(CLIENT) +
         "m=u.module_from_spec(s); s.loader.exec_module(m);"
         "\nimport builtins\n"
         "m.main=lambda argv=None: (sys.stdout.write('x'*(1<<22)), 0)[1]\n"
         "raise SystemExit(m._run())"],
        stdout=_sp.PIPE, stderr=_sp.PIPE,
    )
    proc.stdout.close()          # the `head` moment: reader goes away mid-write
    _, err = proc.communicate()
    assert b"BrokenPipeError" not in err, err.decode("utf-8", "replace")[:400]
    assert proc.returncode == 0, "a caller truncating our output is not our failure"


def test_provenance_names_the_config_file_but_never_the_key(monkeypatch, tmp_path):
    """凭据从哪来必须说得出 —— 否则 401 又变回「说不清是 key 被吊销还是找错了文件」。

    只说路径,永远不说 key 本身:打印出来就进了聊天记录和截图,那比它待着的那个文件
    暴露面大得多。
    """
    config = tmp_path / ".dba-skill" / "config"
    config.parent.mkdir(parents=True)
    config.write_text("PROJECT_API_KEY=secret-from-file\n", encoding="utf-8")

    monkeypatch.setattr(client_module.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("PROJECT_API_KEY", raising=False)
    monkeypatch.delenv("PROJECT_API_BASE_URL", raising=False)
    monkeypatch.setattr(client_module, "_ENV_FILES_LOADED", False)
    monkeypatch.setattr(client_module, "_ENV_PROVENANCE",
                        {"source": None, "searched": [], "keys": [], "sources": {}})
    monkeypatch.chdir(tmp_path)

    prov = client_module._credential_provenance()

    assert str(config) in json.dumps(prov, ensure_ascii=False), prov
    assert prov["user_config"]["exists"] is True
    assert prov["per_key_source"]["PROJECT_API_KEY"] == str(config)
    assert "secret-from-file" not in json.dumps(prov, ensure_ascii=False)


def test_all_says_so_when_the_page_guard_stopped_it(monkeypatch, capsys):
    """--all 撞到分页保护时必须报 partial_result。

    此前它直接 return,绕过了截断警告 —— 于是「拿到 10200/84715 行、stderr 空的」。这比
    普通截断更危险:不加 --all 时调用方至少知道自己只看了一页,加了 --all 是承诺走到底然后
    默默停在 12%。_fetch_all 的注释自己写着「a partial answer that says it is partial」。
    """
    monkeypatch.setattr(client_module, "_FETCH_ALL", True)
    monkeypatch.setattr(client_module, "_MAX_PAGES", 2)

    pages = {"n": 0}

    def fake_once(method, path, params=None, body=None):
        pages["n"] += 1
        offset = int((params or {}).get("offset") or 0)
        return {"items": [{"id": offset + i} for i in range(2)],
                "total": 100, "limit": 2, "offset": offset}

    monkeypatch.setattr(client_module, "_request_once", fake_once)
    payload = client_module._request("GET", "/data-quality", params={})

    assert len(payload["items"]) == 6, "首页 + 2 页保护上限"
    err = capsys.readouterr().err
    assert "partial_result" in err
    assert "6 of 100" in err
    # 建议必须对得上调用方已经做过的事 —— 对已经加了 --all 的人说「re-run with --all」,
    # 正是训练人忽略警告的方式。
    assert "--max-pages" in err and "re-run with --all" not in err


def test_count_only_drops_the_rows_but_keeps_the_counts(monkeypatch):
    """「有多少条」不该为了回答而把 85128 行拖进上下文。"""
    monkeypatch.setattr(client_module, "_COUNT_ONLY", True)
    out = client_module._counts_only(
        {"items": [{"id": 1}, {"id": 2}], "total": 85128, "truncated": True, "limit": 200}
    )
    assert "items" not in out
    assert out["total"] == 85128 and out["truncated"] is True
    assert out["returned_rows"] == 2 and out["rows_omitted_by"] == "--count-only"


def test_count_only_does_not_argue_against_what_the_caller_asked_for(monkeypatch, capsys):
    """用了 --count-only 的人要的就是 total,而且已经拿到了完整的 total。

    再劝他「re-run with --all」是反向建议 —— 和 --all 那条修的是同一类问题:文案没有按
    调用方式分支。一条与请求相矛盾的警告,是训练人忽略警告的另一种方式。
    """
    monkeypatch.setattr(client_module, "_COUNT_ONLY", True)
    client_module._warn_if_truncated(
        {"items": [{"id": 1}], "total": 84728, "truncated": True}, "/data-quality"
    )
    assert capsys.readouterr().err == ""

    # 没用 --count-only 时照常提醒
    monkeypatch.setattr(client_module, "_COUNT_ONLY", False)
    client_module._warn_if_truncated(
        {"items": [{"id": 1}], "total": 84728, "truncated": True}, "/data-quality"
    )
    assert "partial_result" in capsys.readouterr().err


def test_group_by_counts_missing_rows_instead_of_dropping_them():
    """分组时把缺字段的行悄悄丢掉,会让分母无声变小 —— 分组结果开始说谎正是从这里开始。"""
    payload = {"items": [
        {"verdict": "ok"}, {"verdict": "ok"}, {"verdict": "at_risk"},
        {"no_verdict_here": 1}, {"verdict": None},
    ]}
    out = client_module._group_rows(payload, "verdict")
    counts = {g["value"]: g["count"] for g in out["items"]}
    assert counts == {"ok": 2, "at_risk": 1, "(missing)": 1, "(null)": 1}
    assert out["rows_grouped"] == 5 == sum(counts.values()), "每一行都必须被算进去"


def test_group_by_reads_dotted_paths():
    out = client_module._group_rows(
        {"items": [{"local": {"sync_stale": True}}, {"local": {"sync_stale": False}},
                   {"local": {"sync_stale": True}}]},
        "local.sync_stale")
    assert {g["value"]: g["count"] for g in out["items"]} == {"True": 2, "False": 1}


def test_sort_by_puts_rows_without_the_field_last_in_both_directions():
    """「这一行没有这个值」和「这一行是最小的」是两件事。缺字段当 0 处理会把它排到榜首或
    榜尾,读者据此下结论 —— 所以两个方向都让它沉底。"""
    rows = [{"n": 5}, {"other": 1}, {"n": 20}, {"n": None}]
    asc = client_module._sort_rows({"items": rows}, "n", False)["items"]
    desc = client_module._sort_rows({"items": rows}, "n", True)["items"]
    assert [r.get("n") for r in asc][:2] == [5, 20]
    assert [r.get("n") for r in desc][:2] == [20, 5]
    for out in (asc, desc):
        assert all("n" not in r or r["n"] is None for r in out[2:]), "缺值的行留在末尾"


def test_csv_is_written_for_a_spreadsheet_not_a_terminal():
    """--format table 截断到 60 字符、把列表渲染成 JSON —— 终端里对,发给负责人的文件里不对:
    名字不能在第 60 个字符处被切断。"""
    csv_text = client_module._to_csv({"items": [
        {"id": 9, "owners": ["李太平", "谢涛燕"], "note": None},
        {"id": 27, "owners": [], "note": "x" * 80},
    ]})
    lines = csv_text.strip().splitlines()
    assert lines[0] == "id,owners,note"
    assert "李太平; 谢涛燕" in lines[1]
    assert lines[1].endswith(",")           # None → 空单元格
    assert "x" * 80 in lines[2], "不截断"


def test_csv_refuses_a_single_object_instead_of_inventing_a_table():
    assert client_module._to_csv({"version": "3.57.0", "uptime_seconds": 1}) is None


def test_diff_finds_real_changes_and_ignores_the_clock():
    """两次相隔几秒的调用曾报出 48 行「变化」,全是 sync_age_seconds 在逐秒递增 —— 噪声
    把真正出事的那几行埋掉了。时钟派生字段按名忽略,并在结果里列出忽略了哪些:
    悄悄跳过字段,是 diff 以遗漏的方式说谎。
    """
    before = {"items": [
        {"instance_id": 9, "verdict": "suppressed", "local": {"sync_age_seconds": 100.0}},
        {"instance_id": 14, "verdict": "at_risk", "local": {"sync_age_seconds": 100.0}},
        {"instance_id": 99, "verdict": "ok"},
    ]}
    after = {"items": [
        {"instance_id": 9, "verdict": "at_risk", "local": {"sync_age_seconds": 999.0}},
        {"instance_id": 14, "verdict": "at_risk", "local": {"sync_age_seconds": 999.0}},
        {"instance_id": 208, "verdict": "not_applicable"},
    ]}
    d = client_module._diff_payloads(before, after)

    assert [r["instance_id"] for r in d["added"]] == [208]
    assert [r["instance_id"] for r in d["removed"]] == [99]
    assert [c["identity"] for c in d["changed"]] == ["instance_id=9"], "只有真变的那行"
    assert d["changed"][0]["fields"]["verdict"] == {"from": "suppressed", "to": "at_risk"}
    assert d["unchanged"] == 1
    assert "sync_age_seconds" in d["ignored_fields"]


def test_diff_says_when_a_release_may_have_moved_the_verdict_rather_than_the_estate():
    """生产实况:同一查询相隔 5 小时,三台实例判定变了 —— 数据没变,是我发版改了判定逻辑。

    把这两种变化报成一样,读者会开始跳过这份 diff,而那正是这个功能存在的理由。
    """
    before = {"items": [{"instance_id": 97, "verdict": "at_risk"}]}
    after = {"items": [{"instance_id": 97, "verdict": "not_applicable"}]}
    d = client_module._annotate_judgement_changes(
        client_module._diff_payloads(before, after), "3.50.0", "3.50.1")

    assert d["platform_version_changed"] == "3.50.0 → 3.50.1"
    assert d["possibly_judgement_not_estate"] == ["instance_id=97"]
    assert "redefined" in d["note"]

    # 同版本之间的变化不该被这样开脱
    same = client_module._annotate_judgement_changes(
        client_module._diff_payloads(before, after), "3.50.1", "3.50.1")
    assert "possibly_judgement_not_estate" not in same


def test_rows_without_an_identity_are_reported_not_paired_by_position():
    """按位置配对会凭空造出「变化」。没有身份就说没有身份。"""
    d = client_module._diff_payloads(
        {"items": [{"note": "a"}, {"note": "b"}]},
        {"items": [{"note": "b"}, {"note": "a"}]},
    )
    assert d["uncomparable_rows"] == 4
    assert d["added"] == [] and d["removed"] == [] and d["changed"] == []


def test_fan_out_accounts_for_every_requested_instance():
    """扇出时把失败的那台悄悄丢掉,会给出一个「长得和完整答案一样」的短名单 —— 这个客户端
    这一周反复在改的就是这个形状。实时库读取有 30/分钟、单实例 10/分钟的限流,宽扇出必然
    会有被拒的,所以每个 id 要么在 items 要么在 failed。
    """
    import argparse as _ap

    calls = []

    def fake(ns):
        calls.append(ns.instance_id)
        if ns.instance_id == 27:
            raise RuntimeError("HTTP 429 rate limited")
        return {"determination": "verified", "id": ns.instance_id}

    args = _ap.Namespace(func=fake, instance_id=None)
    out = client_module._fan_out(args, [14, 27, 206])

    assert out["requested"] == [14, 27, 206] and calls == [14, 27, 206]
    assert out["returned"] == 2 and out["partial"] is True
    assert [f["instance_id"] for f in out["failed"]] == [27]
    assert "429" in out["failed"][0]["error"]
    # 每个 id 都有交代,一个都不少
    accounted = {r["instance_id"] for r in out["items"]} | {f["instance_id"] for f in out["failed"]}
    assert accounted == {14, 27, 206}


def test_instance_ids_rejects_junk_instead_of_guessing():
    import pytest as _pytest

    assert client_module._parse_instance_ids("14, 20 ,20,27") == [14, 20, 27], "去重且保序"
    with _pytest.raises(SystemExit):
        client_module._parse_instance_ids("14,abc")
    with _pytest.raises(SystemExit):
        client_module._parse_instance_ids("")


def test_snapshot_fingerprint_ignores_flags_that_only_shape_the_output():
    """指纹漏掉一个输出类参数,会把「同一个问题」拆成两个 —— 表现为一份明明存在的快照
    报「没有快照」。这条在实测中真的发生过(--only-if-changed 当时不在排除表里)。
    """
    base = ["get", "/dba/backups/coverage", "--param", "limit=500"]
    fp = client_module._snapshot_fingerprint(base)
    for extra in (["--snapshot"], ["--only-if-changed"], ["--desc"], ["--count-only"],
                  ["--all"], ["--fields", "id"], ["--format", "csv"],
                  ["--group-by", "verdict"], ["--sort-by", "id"], ["--max-pages", "9"]):
        assert client_module._snapshot_fingerprint(extra + base) == fp, extra
    # 但改变问题本身的参数必须换指纹
    assert client_module._snapshot_fingerprint(base + ["--param", "verdict=at_risk"]) != fp


def test_envelope_recognises_every_collection_key_the_platform_uses():
    """SKILL.md 列了九种集合键,而 _envelope 只实现了两种 —— 文档说一套、代码做一套。

    后果是 --sort-by / --group-by 在 probe-run(rows)、elk-search(hits)、timeline(events)
    上静默无效,而那恰恰是最需要排序的地方:段按大小、慢 SQL 按耗时、日志按级别。
    """
    for key in ("items", "rows", "instances", "events", "entries", "results", "hits", "probes"):
        rows, meta = client_module._envelope({key: [{"a": 1}], "total": 1})
        assert rows == [{"a": 1}], key
        assert meta["items_key"] == key
        assert meta["total"] == 1

    assert client_module._envelope([{"a": 1}])[0] == [{"a": 1}]
    assert client_module._envelope({"version": "3.57.0"})[0] is None, "单对象不是集合"


def test_sort_and_group_fail_loudly_on_a_single_object():
    """--format csv 早就在这种情况下报错;另外两个开关却静默返回原样,同一个状况两种待遇。

    静默无效意味着 exit 0、输出与裸调用逐字节相同、stderr 空 —— 使用者没有任何线索。
    """
    import pytest as _pytest

    for call in (lambda: client_module._sort_rows({"version": "1"}, "x", False),
                 lambda: client_module._group_rows({"version": "1"}, "x")):
        with _pytest.raises(SystemExit):
            call()


def test_numeric_strings_sort_as_numbers_not_as_text():
    """Oracle 探针把 size_gb 返回成字符串 "109.67"。按文本比较会把 "44.82" 排在它前面 ——
    一个错误的顺序比不排序更糟,因为它看起来像个答案。
    """
    rows = [{"size_gb": "44.82"}, {"size_gb": "109.67"}, {"size_gb": "9.5"}]
    out = client_module._sort_rows({"rows": rows}, "size_gb", True)["rows"]
    assert [r["size_gb"] for r in out] == ["109.67", "44.82", "9.5"]

    # 但日期不能被当成数字
    days = [{"day": "2026-08-29"}, {"day": "2026-09-03"}, {"day": "2026-09-01"}]
    out = client_module._sort_rows({"rows": days}, "day", True)["rows"]
    assert [r["day"] for r in out] == ["2026-09-03", "2026-09-01", "2026-08-29"]


def test_sorted_rows_go_back_under_their_own_key():
    """按列表长度猜要放回哪个键,会在响应带两个等长列表时改错字段。"""
    payload = {"rows": [{"n": 2}, {"n": 1}], "other_list": [{"n": 9}, {"n": 8}]}
    out = client_module._sort_rows(payload, "n", False)
    assert [r["n"] for r in out["rows"]] == [1, 2]
    assert out["other_list"] == [{"n": 9}, {"n": 8}], "别的列表一动不动"


def test_batch_mode_tells_you_the_field_name_instead_of_making_you_infer_it():
    """--instance-ids 把每台的答案包成 {instance_id, result},于是单台模式下能用的字段名
    在批量下都要加一层 result. —— 两种模式字段名不一致。

    可用字段列表能让人自己发现,但工具本来就知道答案,说出来比让人推更好。
    """
    import pytest as _pytest

    payload = {"items": [
        {"instance_id": 14, "result": {"determination": "verified", "local": {"status": "ok"}}},
        {"instance_id": 20, "result": {"determination": "unknown", "local": {"status": None}}},
    ]}
    with _pytest.raises(SystemExit) as exc:
        client_module._project(payload, ["instance_id", "determination"])
    message = str(exc.value)
    assert "determination → result.determination" in message

    # 多级路径本来就该能用
    out = client_module._project(payload, ["instance_id", "result.local.status"])
    assert [r["result.local.status"] for r in out["items"]] == ["ok", None]


def test_onboarding_check_does_not_hang_permanent_false_gaps_on_cloud_rds(monkeypatch):
    """云 RDS 没有主机可装日志采集器、备份归厂商管 —— 「没有日志」「没标 backup_method」是正确
    状态,不是缺口。报成 missing 会让 115 台云实例常年挂着两个假缺口,而一份永远不会变绿的
    清单,人会停止阅读它。

    ★判定读平台(ELK 行的 applicable、备份的 not_applicable verdict),不在客户端重新实现
    「是不是云 RDS」—— 一份规则两处实现必然漂移。
    """
    import argparse as _ap

    responses = {
        "/instances/42": {"host": "rm-x.mysql.rds.aliyuncs.com", "contact_person": "张三",
                          "database_inventory_coverage": "owns"},
        "/databases": {"items": [{"id": 1}], "total": 3},
        "/instances/42/backups": {"backup_method": None},
        "/elk/coverage": {"instances": [
            {"id": 42, "covered": False, "applicable": False,
             "not_applicable_reason": "cloud RDS: no host to ship from"},
        ]},
        "/dba/backups/coverage": {"items": [
            {"instance_id": 42, "verdict": "not_applicable",
             "reasons": ["cloud RDS: backups are managed by the provider"]},
        ]},
    }
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: responses.get(path))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=42))

    assert out["missing"] == [], "云实例不该挂假缺口"
    assert sorted(out["not_applicable"]) == ["backup_method", "elk_logs"]
    assert "provider" in out["checks"]["backup_method"]["not_applicable"]
    assert "no host" in out["checks"]["elk_logs"]["not_applicable"]
    # 不适用 ≠ 判断不了:unknown 里不该混进它们
    assert out["unknown"] == []


def test_onboarding_check_still_reports_a_real_gap_on_a_self_managed_instance(monkeypatch):
    """把不适用挑出去,不能顺手把真缺口也挑出去。"""
    import argparse as _ap

    responses = {
        "/instances/27": {"host": "10.101.96.21", "contact_person": None,
                          "database_inventory_coverage": None},
        "/databases": {"items": [], "total": 0},
        "/instances/27/backups": {"backup_method": None},
        "/elk/coverage": {"instances": [{"id": 27, "covered": False, "applicable": True}]},
        "/dba/backups/coverage": {"items": [{"instance_id": 27, "verdict": "at_risk"}]},
    }
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: responses.get(path))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=27))
    assert sorted(out["missing"]) == ["backup_method", "database_inventory", "elk_logs",
                                      "ownership"]
    assert out["not_applicable"] == []


def test_onboarding_check_keeps_unreadable_apart_from_uncovered(monkeypatch):
    """读不到 /elk/coverage 和「确实没上报日志」必须是两种输出 —— 这条 unavailable 分支
    本来就是为此存在的,加 not_applicable 时不能把它压掉。"""
    import argparse as _ap

    monkeypatch.setattr(client_module, "_try_get", lambda path, params=None: (
        {"host": "10.0.0.1", "contact_person": "x"} if path.startswith("/instances/9") and
        path.endswith("9") else None))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=9))
    elk = out["checks"]["elk_logs"]
    assert elk["ok"] is None and "unavailable" in elk
    assert "elk_logs" in out["unknown"] and "elk_logs" not in out["not_applicable"]


def test_a_throttled_dependency_is_not_reported_as_a_missing_backup_method(monkeypatch):
    """★ 2026-09-03 生产:全网 195 台扫一遍,报「17 台云 RDS 没声明 backup_method」。

    平台对这 114 台云实例全部答的是 not_applicable —— 那 17 台的区别只是问得太快撞了 429。
    读不到的依赖被当成了确认的缺口:同一个形状,相反的含义。所以依赖不可读时这一项必须是
    unknown,而且 `ok` 不能为真。
    """
    import argparse as _ap

    def throttled(path, params=None):
        if path == "/dba/backups/coverage":
            return {"unavailable": True, "path": path, "status_code": 429}
        if path == "/instances/42":
            return {"host": "rm-x", "contact_person": "张三",
                    "database_inventory_coverage": "owns"}
        if path == "/databases":
            return {"items": [{"id": 1}], "total": 3}
        if path == "/instances/42/backups":
            return {"backup_method": None}
        if path == "/elk/coverage":
            return {"instances": [{"id": 42, "covered": False, "applicable": False}]}
        return {}

    monkeypatch.setattr(client_module, "_try_get", throttled)
    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=42))

    assert "backup_method" not in out["missing"], "读不到 ≠ 缺失"
    assert out["checks"]["backup_method"]["ok"] is None
    assert out["checks"]["backup_method"]["unavailable"]
    assert "backup_method" in out["unknown"] and out["unavailable"] == ["backup_method"]
    assert out["ok"] is False, "答不上来的检查不是一张干净的体检单"


def test_cluster_component_ownership_points_at_the_cluster_head(monkeypatch):
    """一个没有负责人的 TiDB 集群,在组件级展开成 21 条一模一样的「没有负责人」。

    组件的归属属于集群头,和它的备份一样(平台已经把备份判成 not_applicable)。21 条假缺口
    会把唯一那条能有人去处理的真缺口埋掉。
    """
    import argparse as _ap

    responses = {
        "/instances/100": {"host": "10.101.2.101", "contact_person": None, "cluster_id": 18,
                           "database_inventory_coverage": "cluster_component"},
        "/databases": {"items": [], "total": 0},
        "/instances/100/backups": {"backup_method": None},
        "/elk/coverage": {"instances": [{"id": 100, "covered": False, "applicable": False,
                                         "not_applicable_reason": "cluster component"}]},
        "/dba/backups/coverage": {"items": [
            {"instance_id": 100, "verdict": "not_applicable",
             "reasons": ["cluster component: backup is taken at the cluster level"]}]},
    }
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: responses.get(path))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=100))
    assert out["missing"] == []
    assert sorted(out["not_applicable"]) == ["backup_method", "elk_logs", "ownership"]
    assert "18" in out["checks"]["ownership"]["not_applicable"], "要指向集群头,别只说不适用"
    # 集群成员的库存归属者持有库,空列表是正确状态
    assert out["checks"]["database_inventory"]["ok"] is True


def test_a_fan_out_says_when_an_answer_was_built_on_an_unreadable_dependency(monkeypatch):
    """`returned=195, partial=false` 说全都答上了,而中途有依赖被限流没读到。

    每台实例都返回了对象,所以 `failed` 是空的 —— 但其中一部分答案是残的。partial 必须为真。
    """
    import argparse as _ap

    calls = {"n": 0}

    def sometimes_throttled(path, params=None):
        if path == "/dba/backups/coverage":
            calls["n"] += 1
            return {"unavailable": True, "path": path, "status_code": 429}
        if path.startswith("/instances/") and path.endswith("/backups"):
            return {"backup_method": "rman"}
        if path.startswith("/instances/"):
            return {"host": "h", "contact_person": "x", "database_inventory_coverage": "owns"}
        if path == "/databases":
            return {"items": [{"id": 1}], "total": 1}
        if path == "/elk/coverage":
            return {"instances": [{"id": 7, "covered": True}, {"id": 8, "covered": True}]}
        return {}

    monkeypatch.setattr(client_module, "_try_get_shared", sometimes_throttled)
    monkeypatch.setattr(client_module, "_try_get", sometimes_throttled)
    args = _ap.Namespace(instance_id=None, func=client_module.cmd_onboarding_check)
    out = client_module._fan_out(args, [7, 8])

    assert out["returned"] == 2 and not out.get("failed")
    assert out["partial"] is True, "答案是残的就不能说完整"
    assert out["degraded_instances"] == [7, 8]
    assert out["items"][0]["degraded"] == [{"check": "backup_method"}]


def test_a_fleet_wide_table_is_fetched_once_per_fan_out(monkeypatch):
    """扇出 195 台时,每台各拉一次全网备份/ELK 覆盖表 = 390 次全网查询,正是它自己撞限流的原因。"""
    seen = []

    def counting(path, params=None):
        seen.append(path)
        return {"items": [], "instances": []}

    monkeypatch.setattr(client_module, "_try_get", counting)
    for _ in range(5):
        client_module._try_get_shared("/dba/backups/coverage", {"limit": 2000})
    assert seen == ["/dba/backups/coverage"], "同一张全网表只拉一次"

    # 失败不进缓存:一次限流不能让整轮扇出都拿着「读不到」
    seen.clear()
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: (seen.append(path),
                                                   {"unavailable": True})[1])
    client_module._try_get_shared("/elk/coverage")
    client_module._try_get_shared("/elk/coverage")
    assert len(seen) == 2


def test_rate_limited_requests_are_retried_then_reported(monkeypatch):
    """429 的语义是「什么都没执行」,所以重试是安全的,而放着不重试等于把限流变成结论。"""
    monkeypatch.setattr(client_module.time, "sleep", lambda _s: None)

    attempts = {"n": 0}

    def flaky(method, path, params=None, body=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise client_module._RateLimited(0)
        return {"ok": True}

    monkeypatch.setattr(client_module, "_http_call", flaky)
    assert client_module._request_once("GET", "/instances") == {"ok": True}
    assert attempts["n"] == 3

    def always(method, path, params=None, body=None):
        raise client_module._RateLimited(0)

    monkeypatch.setattr(client_module, "_http_call", always)
    try:
        client_module._request_once("GET", "/instances")
    except SystemExit:
        pass
    else:  # pragma: no cover - the point of the test
        raise AssertionError("耗尽配额必须响亮地失败,不能悄悄返回空")


def test_retry_after_is_honoured_but_not_obeyed_indefinitely():
    """服务端说等 5 秒就等 5 秒;说等 10 分钟是让我们放弃,不是让我们挂在那里。"""
    class _H:
        def __init__(self, value): self._v = value
        def get(self, _k): return self._v

    assert client_module._retry_after_seconds(_H("5")) == 5.0
    assert client_module._retry_after_seconds(_H("600")) == 0.0
    assert client_module._retry_after_seconds(_H(None)) == 0.0
    assert client_module._retry_after_seconds(_H("Wed, 21 Oct 2026 07:28:00 GMT")) == 0.0
    assert client_module._retry_after_seconds(None) == 0.0


def test_a_retired_instance_is_not_a_pile_of_onboarding_gaps(monkeypatch):
    """status=inactive 的实例不在被纳管的过程中,平台的覆盖表也只列 active。

    拿它对着覆盖表判,得到三条谁也关不掉的「缺口」—— 而这份清单的用法就是把它做到零。
    """
    import argparse as _ap

    responses = {
        "/instances/209": {"host": "10.24.97.51", "status": "inactive",
                           "contact_person": None, "database_inventory_coverage": "owns"},
        "/databases": {"items": [], "total": 0},
        "/instances/209/backups": {"backup_method": None},
        "/elk/coverage": {"instances": [{"id": 209, "covered": False, "applicable": False}]},
        "/dba/backups/coverage": {"items": []},
    }
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: responses.get(path))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=209))
    assert out["missing"] == [] and out["unknown"] == []
    assert len(out["not_applicable"]) == 4 and out["status"] == "inactive"


def test_an_active_instance_absent_from_the_coverage_table_is_unknown(monkeypatch):
    """表读得到、里面没有这台 —— 这不构成任何证据,不能拿来判「没声明备份方式」。"""
    import argparse as _ap

    responses = {
        "/instances/8": {"host": "10.0.0.8", "status": "active", "contact_person": "x",
                         "database_inventory_coverage": "owns"},
        "/databases": {"items": [{"id": 1}], "total": 1},
        "/instances/8/backups": {"backup_method": None},
        "/elk/coverage": {"instances": [{"id": 8, "covered": True}]},
        "/dba/backups/coverage": {"items": [{"instance_id": 999, "verdict": "ok"}]},
    }
    monkeypatch.setattr(client_module, "_try_get",
                        lambda path, params=None: responses.get(path))

    out = client_module.cmd_onboarding_check(_ap.Namespace(instance_id=8))
    assert out["missing"] == []
    assert out["unknown"] == ["backup_method"] and out["ok"] is False


class YearCoverageTest(unittest.TestCase):
    """`--yoy` 的 partial_reason 判据。

    这段判据被连续找出**四**个洞,每一个的形状都一样:**规则各管一头,交界处没人管**。
      1. FP-1  「当前年且从 1 月起」和「起点年且到 12 月止」两条,遇到一年同时是两者时都不命中
      2. FP-3  awaiting_final_cycle 又默认了 lo == 1,年中接入的部署永远满足不了
      3. FN-1  hi_ok 写成 `hi == 12 or is_now`,is_now 成了无条件通行证 —— 今年断采被吸收成"还没过完"
      4. 标签  按"哪个标志为真"贴,而不是按"实际用了哪条放宽",于是 01..09 的单年序列被说成
               "早于起点的月份永不补齐",而它根本没有更早的月份

    所以这些用例分两组,缺一不可:**该报的必须报**(否则真缺口被静默),
    **不该报的必须不报**(否则每次有人白查一趟)。只有一组的话,任何一次"修"都能靠倒向
    另一边来通过。
    """

    @staticmethod
    def _year(y):
        return {"year": y, "gross": 10.0, "paid": 5.0, "coupon": 0.0}

    @staticmethod
    def _months(y, ms):
        return [{"cycle": "%s-%02d" % (y, i)} for i in ms]

    def _reasons(self, years, months, now=None):
        import datetime as _dt
        from unittest import mock
        if now is None:
            now = _dt.datetime(2026, 9, 8)

        class _Frozen(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with mock.patch.object(client_module, "datetime", _Frozen):
            rows = client_module._year_on_year(years, months)
        return {r["year"]: r.get("partial_reason") for r in rows}

    # ---- 该报的必须报 ---------------------------------------------------------
    def test_current_year_that_stopped_collecting_is_a_gap(self):
        """★ FN-1:今年 4–9 月全缺,却被 `hi == 12 or is_now` 吸收成"年还没过完"。
        真缺口被说成正常,比误报贵得多。"""
        for last, tag in ((4, "01..03"), (7, "01..06")):
            got = self._reasons([self._year("2026")], self._months("2026", range(1, last)))
            self.assertEqual(got["2026"], "missing_months", tag)

    def test_a_hole_in_the_middle_is_a_gap(self):
        got = self._reasons([self._year("2022"), self._year("2023")],
                            self._months("2022", range(1, 13)) + self._months("2023", [8, 9, 11, 12]))
        self.assertEqual(got["2023"], "missing_months")

    def test_a_year_with_no_months_at_all_is_the_loudest_gap(self):
        """整年缺失曾经是唯一连 partial 都不标的一种 —— 缺口最大的那种反而看不见。"""
        got = self._reasons([self._year("2022"), self._year("2023"), self._year("2024")],
                            self._months("2022", range(1, 13)) + self._months("2024", range(1, 13)))
        self.assertEqual(got["2023"], "missing_months")

    def test_a_series_that_stops_in_a_past_year_is_a_gap(self):
        got = self._reasons([self._year("2023"), self._year("2024")],
                            self._months("2023", range(1, 13)) + self._months("2024", range(1, 7)))
        self.assertEqual(got["2024"], "missing_months")

    def test_starting_mid_year_is_only_excused_for_the_first_year(self):
        got = self._reasons([self._year("2022"), self._year("2023"), self._year("2024")],
                            self._months("2022", range(1, 13)) + self._months("2023", range(3, 13))
                            + self._months("2024", range(1, 13)))
        self.assertEqual(got["2023"], "missing_months")

    # ---- 不该报的必须不报 -----------------------------------------------------
    def test_a_deployment_onboarded_mid_year_is_not_a_gap(self):
        """★ FP-1:起点年即当前年 —— 既到不了 12 月也不从 1 月起,两条规则都不命中。
        本项目自己就差点是这个形状(数据起点 2021-08)。"""
        got = self._reasons([self._year("2026")], self._months("2026", range(3, 10)))
        self.assertEqual(got["2026"], "series_start_in_progress")

    def test_the_current_month_may_not_have_been_billed_yet(self):
        """留一个月余量:当月账期可能还没出账。"""
        got = self._reasons([self._year("2026")], self._months("2026", range(1, 9)))
        self.assertEqual(got["2026"], "year_in_progress")

    def test_a_full_january_start_is_not_called_a_series_start(self):
        """★ 标签按**实际用了哪条放宽**贴,不按哪个标志为真。一个只有 2026 一年、从 1 月起的
        序列既是 earliest 也是 current_year,但下界压根不需要"起点年"这条豁免 ——
        标成 series_start_in_progress 是在说"早于起点的月份永不补齐",而它没有更早的月份。"""
        self.assertEqual(self._reasons([self._year("2026")],
                                       self._months("2026", range(1, 10)))["2026"],
                         "year_in_progress")
        import datetime as _dt
        self.assertEqual(self._reasons([self._year("2026")], self._months("2026", [1]),
                                       now=_dt.datetime(2026, 1, 20))["2026"],
                         "year_in_progress")

    def test_the_real_production_shape_is_clean(self):
        got = self._reasons([self._year("2021"), self._year("2026")],
                            self._months("2021", range(8, 13)) + self._months("2026", range(1, 10)))
        self.assertEqual(got["2021"], "series_start")
        self.assertEqual(got["2026"], "year_in_progress")

    # ---- 账单滞后:改名,不是消音 ----------------------------------------------
    def test_last_years_december_may_still_be_in_flight_in_january(self):
        """★ FP-3:这条规则也默认了 lo == 1,于是年中接入的部署每逢年初都被报成缺口。
        窗口很窄,而且**仍然标 partial** —— 是改名不是消音。"""
        import datetime as _dt
        got = self._reasons([self._year("2025"), self._year("2026")],
                            self._months("2025", range(6, 12)) + self._months("2026", [1]),
                            now=_dt.datetime(2026, 1, 15))
        self.assertEqual(got["2025"], "awaiting_final_cycle")

    def test_the_billing_lag_window_closes(self):
        """宽限过期就变回缺口 —— 一个永不过期的宽限就是消音。"""
        import datetime as _dt
        got = self._reasons([self._year("2025"), self._year("2026")],
                            self._months("2025", range(6, 12)) + self._months("2026", range(1, 6)),
                            now=_dt.datetime(2026, 5, 15))
        self.assertEqual(got["2025"], "missing_months")

    def test_two_missing_months_is_not_billing_lag(self):
        import datetime as _dt
        got = self._reasons([self._year("2025"), self._year("2026")],
                            self._months("2025", range(1, 11)) + self._months("2026", [1]),
                            now=_dt.datetime(2026, 1, 15))
        self.assertEqual(got["2025"], "missing_months")

    # ---- 对服务端返回形状不做无防御假设 ----------------------------------------
    def test_reverse_order_from_the_server_does_not_swap_the_labels(self):
        """位置式的 [0]/[-1] 在倒序返回时会把 series_start 与 year_in_progress 直接对调:
        两个都错、方向相反、都是误导。"""
        got = self._reasons([self._year("2026"), self._year("2021")],
                            self._months("2026", range(1, 10)) + self._months("2021", range(8, 13)))
        self.assertEqual(got["2021"], "series_start")
        self.assertEqual(got["2026"], "year_in_progress")

    def test_reverse_order_does_not_change_a_single_yoy_number(self):
        """★ 标签只是顺序防线守住的**一半**,而且是不承重的那一半。

        `earliest = min()` 和"等于当前年"都是**按值**比较的,倒序下标签本来就对 —— 所以上面
        那条用例即使把 `sorted` 整条拆掉也照样绿。真正依赖顺序的是 `prev_*` 累加,也就是
        **每一个 pct/delta**:实测把 sorted 去掉后,倒序输入下三年的同比全部算错
        (+25.0/−40.0 变成 +66.7/−20.0),而当时 94 条用例一条都没响。

        变异测试是这么发现的:用例跑绿不等于它能抓回归 —— 得把防线拆掉看它响不响。
        """
        rows = [{"year": "2024", "gross": 80.0, "paid": 80.0, "coupon": 0.0},
                {"year": "2025", "gross": 100.0, "paid": 100.0, "coupon": 0.0},
                {"year": "2026", "gross": 60.0, "paid": 60.0, "coupon": 0.0}]
        months = (self._months("2024", range(1, 13)) + self._months("2025", range(1, 13))
                  + self._months("2026", range(1, 13)))

        def numbers(years):
            out = client_module._year_on_year(years, months)
            return {r["year"]: (r.get("pct"), r.get("delta"),
                                r.get("gross_pct"), r.get("paid_pct")) for r in out}

        forward = numbers(rows)
        self.assertEqual(forward["2025"][0], 25.0)   # 80 → 100
        self.assertEqual(forward["2026"][0], -40.0)  # 100 → 60
        self.assertEqual(numbers(list(reversed(rows))), forward,
                         "服务端换个顺序返回,同比数字就变了")
        self.assertEqual(numbers([rows[1], rows[2], rows[0]]), forward, "乱序同理")

    def test_no_month_series_says_so_instead_of_looking_complete(self):
        """没有月度序列就无法判断完整性。此前整个 partial 机制静默消失,每年都像完整的 ——
        「查不了」和「没问题」必须是两种可见的答案。"""
        rows = client_module._year_on_year([self._year("2025"), self._year("2026")], None)
        for row in rows:
            self.assertTrue(row.get("coverage_unknown"))
            self.assertTrue(row.get("coverage_unknown_reason"))
            self.assertIsNone(row.get("partial"))

    def test_a_non_numeric_year_does_not_crash(self):
        self._reasons([self._year("FY26")], self._months("2026", range(1, 4)))


class YearCoveragePropertyTest(unittest.TestCase):
    """把整个形状空间对着一个**独立 oracle** 扫一遍,而不是手挑几个点。

    上面那 15 条是照着已经找到的四个洞写的 —— 它们能防这四个复发,防不住第五个。而这段判据
    在四轮里被找出四个洞,每一个都是"我们只想到自己想得到的形状"。所以这里换打法:

    oracle 从**另一个角度**描述同一件事,不复用实现里的任何概念(没有 lo_ok/hi_ok/is_first/
    is_now,也不分年份角色)。它只问一句:**整条序列从第一个账期跑到最后一个应有的账期,
    落在这一年里的月份,是不是都在?** 少一个就是缺口。

    两边独立地算同一个事实,不一致就是有一方错了 —— 这比"我写用例、我自己挑形状"强的地方在于,
    它不依赖我事先知道哪里会错。
    """

    @staticmethod
    def _oracle_gap(cycles, year, now):
        """这一年**应该**有哪些月?缺了就是缺口。与实现不共享任何代码。

        序列的起点 = 观测到的最早账期(在那之前本来就没有数据)。
        序列的终点 = 当前月减一(当月账期可能还没出账)。
        """
        if not cycles:
            return False
        first = min(cycles)
        last = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        expected = {
            m for m in range(1, 13)
            if first <= (year, m) <= last
        }
        return bool(expected - {m for (y, m) in cycles if y == year})

    def _reasons(self, cycles, now):
        import datetime as _dt
        from unittest import mock

        class _Frozen(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        years = sorted({y for (y, _) in cycles})
        payload_years = [{"year": str(y), "gross": 10.0, "paid": 5.0, "coupon": 0.0} for y in years]
        months = [{"cycle": "%04d-%02d" % (y, m)} for (y, m) in sorted(cycles)]
        with mock.patch.object(client_module, "datetime", _Frozen):
            return {r["year"]: r.get("partial_reason")
                    for r in client_module._year_on_year(payload_years, months)}

    #: 判据认定"这一年少了账期"的那些原因。awaiting_final_cycle 也在里面:它是同一个事实的
    #: 另一个名字(账单在途),不是"没有缺"——把它算成不缺,就等于承认那条宽限是消音。
    GAP_REASONS = {"missing_months", "awaiting_final_cycle"}

    @staticmethod
    def _in_the_future(cycles, now):
        """账期晚于当前月 = 未来数据。

        ★ 这类形状**被显式排除在穷举之外**,而不是让它悄悄通过:账单是为已结束的周期出的,
        平台产不出未来账期,所以两边在这个区域各说各话都不算错(实现说"那你缺了 1 月",
        oracle 说"这一年还什么都不该有")。为一个谁都没决定过的行为写断言,只会把测试变成
        实现的复读机。**排除的是形状,不是问题** —— 边界写在这里,下次真出现了能查到。
        """
        return any((y, m) > (now.year, now.month) for (y, m) in cycles)

    def test_gap_detection_matches_an_independent_oracle(self):
        """连续区间 × 年份角色 × 当前月的穷举对判。"""
        import datetime as _dt
        checked = 0
        for now_month in range(1, 13):
            now = _dt.datetime(2026, now_month, 15)
            for lo in range(1, 13):
                for hi in range(lo, 13):
                    for prior in ([], [(2025, m) for m in range(1, 13)]):
                        cycles = prior + [(2026, m) for m in range(lo, hi + 1)]
                        if self._in_the_future(cycles, now):
                            continue
                        reasons = self._reasons(cycles, now)
                        got_gap = reasons.get("2026") in self.GAP_REASONS
                        want_gap = self._oracle_gap(cycles, 2026, now)
                        checked += 1
                        self.assertEqual(
                            got_gap, want_gap,
                            "now=%s 2026年 %d..%d prior=%s → reason=%r,oracle 说 gap=%s"
                            % (now_month, lo, hi, bool(prior), reasons.get("2026"), want_gap))
        self.assertGreater(checked, 700, "扫描面积缩水了,这条就不再是穷举")

    def test_any_hole_is_always_a_gap(self):
        """有洞一律是缺口 —— 这一条没有例外,不依赖年份角色或当前月。

        单独列出来是因为它是最不该被任何"放宽"吃掉的性质:上面四个洞里有三个都是放宽放过头,
        而放宽只该解释**两端**,永远不该解释**中间**。
        """
        import datetime as _dt
        import itertools
        now = _dt.datetime(2026, 9, 15)
        checked = 0
        for size in (3, 4, 5):
            for combo in itertools.combinations(range(1, 13), size):
                if max(combo) - min(combo) + 1 == len(combo):
                    continue  # 连续的,不是这条要管的
                reasons = self._reasons([(2026, m) for m in combo], now)
                checked += 1
                self.assertEqual(reasons.get("2026"), "missing_months",
                                 "月份 %s 中间有洞却没报缺口" % (combo,))
        self.assertGreater(checked, 500)

    def test_a_label_never_claims_something_untrue(self):
        """标签宣称的事实必须成立 —— 说 series_start 就得真是最早年且不从 1 月起,
        说 in_progress 就得真是当前年且没到 12 月。第四个洞正是标签说了它没做的事。"""
        import datetime as _dt
        for now_month in (1, 6, 12):
            now = _dt.datetime(2026, now_month, 15)
            for lo in range(1, 13):
                for hi in range(lo, 13):
                    for prior in ([], [(2025, m) for m in range(1, 13)]):
                        cycles = prior + [(2026, m) for m in range(lo, hi + 1)]
                        if self._in_the_future(cycles, now):
                            continue
                        reason = self._reasons(cycles, now).get("2026")
                        is_first = not prior
                        if reason in ("series_start", "series_start_in_progress"):
                            self.assertTrue(is_first, "非最早年却自称 series_start")
                            self.assertNotEqual(lo, 1, "从 1 月起却自称 series_start")
                        if reason in ("year_in_progress", "series_start_in_progress"):
                            self.assertNotEqual(hi, 12, "已到 12 月却自称 in_progress")


class AppliedFiltersTest(unittest.TestCase):
    """打错一个受控词表的值,不能换来一个理直气壮的 0。

    `alerts --severity nosuch` 曾经返回 `{"counts": {全 0}, "items": [], "total": 0}` ——
    和"确实没有告警"一个字都不差。两条修法,按参数的性质分:

    * **闭合词表**(severity:响应自己的 `counts` 就列着 low/medium/high/critical)→ `choices=`,
      在参数解析时就响掉。
    * **部署相关或会增长的**(environment / metric-name / levels)→ **不能**硬编码 choices,
      那会拒掉合法的新值;改为回显生效的查询,让"我按 X 过滤得到 0"和"总共就是 0"可区分。

    回显的是**生效的查询**而非"用户显式传了什么":默认值恰恰最该说出来 —— 问"有告警吗"
    拿到 0,你得知道它只看了 `status=active`。
    """

    def test_a_closed_vocabulary_rejects_a_typo_instead_of_returning_zero(self):
        import subprocess
        for command in ("alerts", "alerts-list"):
            result = subprocess.run(
                [sys.executable, str(CLIENT), command, "--severity", "nosuch"],
                capture_output=True, text=True,
                env={**os.environ, "PROJECT_API_BASE_URL": "http://127.0.0.1:1", 
                     "PROJECT_API_KEY": "x"},
            )
            self.assertNotEqual(result.returncode, 0, command)
            self.assertIn("invalid choice", result.stderr, command)
            self.assertIn("critical", result.stderr, "报错里没列出合法值")

    def test_output_flags_are_not_echoed_as_filters(self):
        """`--format` / `--fields` 影响的是怎么输出,不是问了什么。把它们混进
        applied_filters,会让人以为结果被它们筛过。"""
        args = argparse.Namespace(
            severity="high", format="table", fields="a,b", count_only=True,
            sort_by="x", func=lambda a: None, _needs_instance=False,
        )
        got = client_module._applied_filters(args)
        self.assertEqual(got, {"severity": "high"})

    def test_commands_with_no_filters_stay_untouched(self):
        args = argparse.Namespace(format="json", count_only=False, func=lambda a: None)
        self.assertEqual(client_module._applied_filters(args), {})


class TopologyAndSeriesHelperTest(unittest.TestCase):
    """两条 helper 各自守住一件"现有答案是错的"的事。"""

    def test_topology_inlines_names_and_names_what_clusters_cannot_see(self):
        """边只带 id;不 inline 名字的话每个调用方都得自己和 nodes 做一次连接,
        而那一步做错了没有任何东西会报错。

        ★ 更要紧的是 coverage:`/clusters` 结构上只能显示**纳管**成员,生产 37 条复制边里
        33 条有一端是 ext: 外部主机。用 /clusters 回答"主备是谁"会给出一个**残缺但看起来
        完整**的答案 —— 这个数就是它看不见的部分。
        """
        from unittest import mock

        payload = {
            "nodes": [{"id": 38, "name": "rds-prd", "external": False, "engine": "mysql"}],
            "edges": [{"from": "ext:10.1.2.3:3306", "to": 38, "kind": "replication",
                       "sync_state": None}],
        }
        args = argparse.Namespace(instance_id=None, ip=None, host=None, external_only=False)
        with mock.patch.object(client_module, "_request", return_value=payload):
            out = client_module.cmd_topology(args)
        edge = out["items"][0]
        self.assertTrue(edge["from"]["external"])
        self.assertEqual(edge["from"]["endpoint"], "10.1.2.3:3306")
        self.assertEqual(edge["to"]["name"], "rds-prd", "纳管一端没有 inline 名字")
        self.assertEqual(out["coverage"]["edges_touching_unmanaged"], 1)
        self.assertIn("/clusters", out["coverage"]["note"])

    def test_topology_uses_the_items_key_so_the_generic_flags_work(self):
        """★ 叫 `edges` 更有描述性,但 _envelope 只认那几个集合键 —— 换个名字会让
        --fields / --group-by / --sort-by / --count-only / --format table **全部静默失效**。
        描述性换来一堆不工作的旗标,不划算。"""
        from unittest import mock

        payload = {"nodes": [], "edges": [{"from": 1, "to": 2, "kind": "replication"}]}
        args = argparse.Namespace(instance_id=None, ip=None, host=None, external_only=False)
        with mock.patch.object(client_module, "_request", return_value=payload):
            out = client_module.cmd_topology(args)
        items, meta = client_module._envelope(out)
        self.assertIsNotNone(items, "_envelope 认不出这个集合,通用旗标会静默失效")
        self.assertEqual(meta.get("items_key"), "items")

    def test_an_instance_with_no_edges_says_which_kind_of_empty_it_is(self):
        """"它确实是单机"和"复制关系没被发现"长得一样。拓扑是从各实例自己上报的主从信息推的,
        一端不上报就整条边都看不到 —— 不能据此断言它没有备库。"""
        from unittest import mock

        args = argparse.Namespace(instance_id=999, ip=None, host=None, external_only=False)
        with mock.patch.object(client_module, "_request",
                               return_value={"nodes": [], "edges": []}):
            out = client_module.cmd_topology(args)
        self.assertEqual(out["items"], [])
        self.assertIn("不要据此断言", out["note"])

    def test_a_422_from_the_series_endpoint_is_the_answer_not_a_failure(self):
        """★ 平台用 422 说明「这个指标没有该粒度的汇总,所以这个窗口什么都给不出」——
        那正是提问者需要知道的。塞进 http_error 里等于把答案降级成报错,读的人会以为
        是自己调错了。"""
        from unittest import mock

        body = json.dumps({"message": "no minute aggregates, so a 48h window has nothing to return"})
        args = argparse.Namespace(instance_id=75, ip=None, host=None,
                                  metric_name="qps", hours=48, granularity=None)
        with mock.patch.object(client_module, "_try_get",
                               return_value={"unavailable": True, "status_code": 422}), \
             mock.patch.object(client_module, "_LAST_HTTP_STATUS", 422), \
             mock.patch.object(client_module, "_LAST_HTTP_BODY", body):
            out = client_module.cmd_metric_series(args)
        self.assertTrue(out["unavailable"])
        self.assertIn("aggregates", out["reason"])
        self.assertIn("不是调用失败", out["hint"])
        self.assertEqual(out["points"], [])


class SeriesHintIsConditionalTest(unittest.TestCase):
    """★ 我自己种下的那类问题:无条件给建议,于是**错的建议**发给了另外两种 422。

    这个端点的 422 不止一种来源:
      - 指标没有该粒度的汇总(该给"用 --hours 24"这条建议)
      - hours 超过平台上限 720(参数校验,和汇总无关)
      - granularity=raw 配大窗口(平台的另一条拒绝)
    第一版无条件加那条建议,于是后两种都被告知"用 --hours 24 拿原始点"——**错的建议比没有建议
    更贵**,它会让人去改一个不相干的参数。

    匹配不上时**不加提示**,让平台自己那句话原样过去:那两条消息本身已经自解释。
    这是 fail-safe 的方向——万一平台改了措辞,结果是"少一条提示",不是"多一条错提示"。
    """

    def _run(self, message: str):
        from unittest import mock

        args = argparse.Namespace(instance_id=75, ip=None, host=None,
                                  metric_name="qps", hours=48, granularity=None)
        with mock.patch.object(client_module, "_try_get",
                               return_value={"unavailable": True, "status_code": 422}), \
             mock.patch.object(client_module, "_LAST_HTTP_STATUS", 422), \
             mock.patch.object(client_module, "_LAST_HTTP_BODY",
                               json.dumps({"message": message})):
            return client_module.cmd_metric_series(args)

    def test_the_aggregates_422_gets_the_hint(self):
        out = self._run("'qps' has raw samples but no 'minute' aggregates, so a 48h window ...")
        self.assertIn("hint", out)
        self.assertIn("--hours 24", out["hint"])

    def test_an_unrelated_422_does_not_get_a_wrong_hint(self):
        """守卫不能只会给建议。这两条如果也拿到"用 --hours 24",人会去改一个不相干的参数。"""
        for message in ("Request validation failed",
                        "granularity=raw is limited to hours<=24 (requested 100)."):
            out = self._run(message)
            self.assertNotIn("hint", out, message)
            self.assertEqual(out["reason"], message, "平台自己那句话没有原样透出去")


class MetricSeriesEmptyKindsTest(unittest.TestCase):
    """★ 同一个形状的第三次:拼错的指标名和"窗口内无样本"返回完全一样的空。

    前两次是 `alerts --severity nosuch` 和 `capacity-forecast --metric-name`。而这个 helper
    存在的理由,正是平台那句 "an empty list here would read as 'not collected' or 'flat',
    which is the opposite of the truth" —— 走 422 的那条路被照顾得很好,走空结果的这条
    一句话都没有。词表离一次 /metrics/{id}/latest 只有一步。
    """

    def _run(self, metric, series, latest):
        from unittest import mock

        args = argparse.Namespace(instance_id=75, ip=None, host=None,
                                  metric_name=metric, hours=24, granularity=None)

        def fake(path, params=None):
            return latest if path.endswith("/latest") else series

        with mock.patch.object(client_module, "_try_get", side_effect=fake):
            return client_module.cmd_metric_series(args)

    LATEST = [{"metric_name": "qps"}, {"metric_name": "tps"}]

    def test_a_typo_is_named_as_a_typo(self):
        out = self._run("zzz_nope", [], self.LATEST)
        self.assertTrue(out["unavailable"])
        self.assertIn("不在这台实例的指标词表里", out["reason"])
        self.assertEqual(out["known_metrics"], ["qps", "tps"], "没把可选值列出来")

    def test_a_real_metric_with_no_samples_says_so_instead(self):
        """守卫不能把正事也挡了:指标是真的,只是这段时间没数据 —— 那是另一种答案。"""
        out = self._run("qps", [], self.LATEST)
        self.assertNotIn("unavailable", out)
        self.assertIn("这段时间没有数据", out["summary"]["note"])

    def test_the_collection_key_is_items_so_the_generic_flags_work(self):
        """★ `points` 更贴切,但 _envelope 认不出它 —— --fields / --group-by / --sort-by /
        --format table 会**全部失效**,而"看趋势"恰恰最需要 --fields collected_at,value。
        同一个教训我在 topology 的注释里写过,又在这条命令上犯了一遍。"""
        out = self._run("qps", [{"metric_name": "qps", "value": 1.0, "granularity": "raw",
                                 "collected_at": "2026-09-08T00:00:00"}], self.LATEST)
        items, meta = client_module._envelope(out)
        self.assertIsNotNone(items)
        self.assertEqual(meta.get("items_key"), "items")


class MetricSeriesUnknownVocabularyTest(unittest.TestCase):
    """★ 我在修「unknown 被当成 ok」的过程中,自己又造了一个 unknown 被当成 ok。

    区分两种空的那段代码只写了两个分支(不在词表 / 在词表),**词表取不到时两个都不进,
    静默落回原样** —— 和修复前那个不区分的裸空一模一样。

    触发时机还特别不巧:那次额外的 `/latest` **只在空结果时才发出**,也就是有人正在逐个
    试指标名的时候 —— 恰恰是最容易撞限流(全局 600/min)的场景。

    ★ fail-safe 的方向是**「说我不知道」**,不是「回到不区分」。
    """

    def _run(self, metric, latest, status=429):
        from unittest import mock

        args = argparse.Namespace(instance_id=75, ip=None, host=None,
                                  metric_name=metric, hours=24, granularity=None)

        def fake(path, params=None):
            return latest if path.endswith("/latest") else []

        with mock.patch.object(client_module, "_try_get", side_effect=fake), \
             mock.patch.object(client_module, "_LAST_HTTP_STATUS", status):
            return client_module.cmd_metric_series(args)

    def test_a_failed_vocabulary_lookup_says_it_could_not_tell(self):
        out = self._run("zzz_nope", {"unavailable": True, "status_code": 429})
        self.assertTrue(out["unavailable"], "词表取不到时静默落回了不区分的空")
        self.assertIn("无法归类", out["reason"])
        self.assertIn("429", out["reason"], "没说清是哪一种失败")
        self.assertIn("重试", out["reason"], "没告诉调用方下一步能做什么")

    def test_an_instance_with_no_metrics_at_all_is_a_different_answer(self):
        """"取词表失败"和"这台一个指标都没采到"混成一句话,会让人去重试一个重试不好的问题。"""
        out = self._run("zzz_nope", [])
        self.assertTrue(out["unavailable"])
        self.assertIn("一个指标都没有", out["reason"])
        self.assertIn("采集是不是停了", out["reason"])
        self.assertNotIn("重试", out["reason"])

    def test_the_working_paths_still_distinguish_the_two_empties(self):
        """守卫不能把正事挡了:词表正常时,两种空仍要分得清。"""
        vocab = [{"metric_name": "qps"}]
        typo = self._run("zzz_nope", vocab)
        self.assertIn("不在这台实例的指标词表里", typo["reason"])
        empty_window = self._run("qps", vocab)
        self.assertNotIn("unavailable", empty_window)
        self.assertIn("这段时间没有数据", empty_window["summary"]["note"])


def test_topology_passes_through_the_inference_marker():
    """★ 平台打 `resolved_by` 的全部理由是让人看得见"这条边是猜的"。
    helper 在中间丢掉它,等于那个标记白加了 —— 第一版正是这么丢的:平台标 2 条,这儿显示 0 条。"""
    from unittest import mock

    payload = {"nodes": [{"id": 2, "name": "sby", "external": False}],
               "edges": [{"from": 1, "to": 2, "kind": "replication",
                          "sync_state": "async", "resolved_by": "address_fallback"}]}
    args = argparse.Namespace(instance_id=None, ip=None, host=None, external_only=False)
    with mock.patch.object(client_module, "_request", return_value=payload):
        out = client_module.cmd_topology(args)
    assert out["items"][0]["resolved_by"] == "address_fallback"
