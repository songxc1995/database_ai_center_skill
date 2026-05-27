import json
import os
import subprocess
import sys
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
        self.env = {
            **os.environ,
            "PROJECT_API_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/api/v2",
            "PROJECT_API_KEY": "super-secret-key",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_client(self, *args):
        return subprocess.run(
            [sys.executable, str(CLIENT), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

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


if __name__ == "__main__":
    unittest.main()
