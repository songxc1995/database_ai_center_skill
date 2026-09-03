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
        self.assertEqual(json.loads(result.stdout), {"ok": True})
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
        return {}

    monkeypatch.setattr(client, "_try_get", fake_get)
    out = client.cmd_onboarding_check(argparse.Namespace(instance_id=5))
    assert out["checks"]["elk_logs"]["ok"] is True
    assert out["ok"] is True

    def broken_elk(path, params=None):
        return {"unavailable": True} if path == "/elk/coverage" else fake_get(path, params)

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
