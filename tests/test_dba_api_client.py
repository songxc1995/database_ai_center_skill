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
