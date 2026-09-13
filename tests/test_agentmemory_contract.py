import http.server
import json
import os
import sys
import threading
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.providers.base import ProviderStatus
from context_forge.providers.experience.agentmemory import AgentMemoryProvider


class TestAgentMemoryContract(unittest.TestCase):
    def test_unreachable_endpoint_returns_unavailable(self) -> None:
        am = AgentMemoryProvider(endpoint_url="http://127.0.0.1:59998")
        self.assertFalse(am.is_available())
        health = am.check_health()
        self.assertEqual(health.status, ProviderStatus.UNAVAILABLE)

    def test_mock_agentmemory_server_contracts(self) -> None:
        """Run in-process mock HTTP server to test strict AgentMemory contracts."""
        class MockHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # silence stderr

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                if self.path == "/agentmemory/health":
                    if auth == "Bearer secret-token-valid":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"version": "1.4.2", "capabilities": ["smart-search"]}).encode("utf-8"))
                    elif auth == "Bearer secret-malformed":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b"not valid json at all")
                    elif auth == "Bearer secret-empty-json":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b"{}")
                    elif auth == "Bearer secret-arbitrary-json":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"foo": "bar", "unknown_service": 123}).encode("utf-8"))
                    elif auth == "Bearer secret-status-ok":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
                    elif auth == "Bearer secret-version-only":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"version": "1.0"}).encode("utf-8"))
                    elif auth == "Bearer secret-degraded":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "degraded", "service": "agentmemory", "version": "1.4.2"}).encode("utf-8"))
                    elif auth == "Bearer secret-server-error":
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error": "Internal Server Error"}')
                    elif auth == "Bearer secret-503-am":
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"service": "agentmemory", "status": "degraded"}).encode("utf-8"))
                    else:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error": "Unauthorized"}')
                elif self.path == "/agentmemory/livez":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "alive"}).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                auth = self.headers.get("Authorization", "")
                if auth not in ("Bearer secret-token-valid", "Bearer secret-degraded"):
                    self.send_response(401)
                    self.end_headers()
                    return

                if self.path == "/agentmemory/smart-search":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    query = body.get("query", "")
                    if query == "empty":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"results": []}).encode("utf-8"))
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "results": [{"content": "Use Redis advisory locks", "id": "mem-1"}]
                        }).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

        server = http.server.HTTPServer(("127.0.0.1", 0), MockHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            url = f"http://127.0.0.1:{port}"

            # 1. Valid auth -> OK
            am_valid = AgentMemoryProvider(endpoint_url=url, secret="secret-token-valid")
            h_ok = am_valid.check_health()
            self.assertEqual(h_ok.status, ProviderStatus.OK)
            self.assertEqual(h_ok.version, "1.4.2")
            self.assertEqual(h_ok.capabilities, ["smart-search"])

            # 2. Malformed HTTP 200 JSON -> MALFORMED
            am_malformed = AgentMemoryProvider(endpoint_url=url, secret="secret-malformed")
            h_mal = am_malformed.check_health()
            self.assertEqual(h_mal.status, ProviderStatus.MALFORMED)
            self.assertEqual(h_mal.version, "")
            self.assertEqual(h_mal.capabilities, [])
            self.assertIn("invalid JSON", h_mal.diagnostic)

            # 3. Empty JSON {} with HTTP 200 -> INCOMPATIBLE (reject arbitrary empty JSON)
            am_empty = AgentMemoryProvider(endpoint_url=url, secret="secret-empty-json")
            h_empty = am_empty.check_health()
            self.assertEqual(h_empty.status, ProviderStatus.INCOMPATIBLE)
            self.assertIn("AgentMemory identity", h_empty.diagnostic)

            # 4. Arbitrary {"foo": "bar"} with HTTP 200 -> INCOMPATIBLE (reject unrelated HTTP services)
            am_arb = AgentMemoryProvider(endpoint_url=url, secret="secret-arbitrary-json")
            h_arb = am_arb.check_health()
            self.assertEqual(h_arb.status, ProviderStatus.INCOMPATIBLE)

            # 5. Arbitrary {"status": "ok"} without agentmemory identity -> INCOMPATIBLE
            am_status_ok = AgentMemoryProvider(endpoint_url=url, secret="secret-status-ok")
            h_status_ok = am_status_ok.check_health()
            self.assertEqual(h_status_ok.status, ProviderStatus.INCOMPATIBLE)

            # 6. Arbitrary {"version": "1.0"} without agentmemory identity -> INCOMPATIBLE
            am_version_only = AgentMemoryProvider(endpoint_url=url, secret="secret-version-only")
            h_version_only = am_version_only.check_health()
            self.assertEqual(h_version_only.status, ProviderStatus.INCOMPATIBLE)

            # 7. Degraded mode {"status": "degraded"} -> DEGRADED and is_available() == True
            am_degraded = AgentMemoryProvider(endpoint_url=url, secret="secret-degraded")
            h_deg = am_degraded.check_health()
            self.assertEqual(h_deg.status, ProviderStatus.DEGRADED)
            self.assertTrue(am_degraded.is_available())
            res_deg = am_degraded.recall_lessons_result("locks")
            self.assertEqual(res_deg.status, ProviderStatus.OK)
            self.assertEqual(len(res_deg.data), 1)

            # 8. HTTP 503 with AgentMemory identity -> DEGRADED
            am_503 = AgentMemoryProvider(endpoint_url=url, secret="secret-503-am")
            h_503 = am_503.check_health()
            self.assertEqual(h_503.status, ProviderStatus.DEGRADED)

            # 9. HTTP 500 -> ERROR
            am_err = AgentMemoryProvider(endpoint_url=url, secret="secret-server-error")
            h_err = am_err.check_health()
            self.assertEqual(h_err.status, ProviderStatus.ERROR)

            # 10. Invalid auth -> UNAUTHORIZED
            am_bad_auth = AgentMemoryProvider(endpoint_url=url, secret="wrong-secret")
            h_bad = am_bad_auth.check_health()
            self.assertEqual(h_bad.status, ProviderStatus.UNAUTHORIZED)

            # 11. Search results
            res = am_valid.recall_lessons_result("locks")
            self.assertEqual(res.status, ProviderStatus.OK)
            self.assertEqual(len(res.data), 1)
            self.assertIn("Redis advisory locks", res.data[0]["finding"])

            # 12. Search zero results -> NO_RESULTS
            res_empty = am_valid.recall_lessons_result("empty")
            self.assertEqual(res_empty.status, ProviderStatus.NO_RESULTS)
            self.assertEqual(res_empty.data, [])

        finally:
            server.shutdown()
            server.server_close()

    def test_live_agentmemory_health_opt_in(self) -> None:
        """Opt-in live AgentMemory health test (RUN_AGENTMEMORY_LIVE_TESTS=1). Fails if enabled and unhealthy."""
        if os.environ.get("RUN_AGENTMEMORY_LIVE_TESTS") != "1":
            self.skipTest("Live AgentMemory tests not enabled. Set RUN_AGENTMEMORY_LIVE_TESTS=1 to run.")

        am = AgentMemoryProvider()
        health = am.check_health()
        if not health.is_ok():
            self.fail(f"Live AgentMemory server not available or healthy: {health.diagnostic}")

        self.assertTrue(health.is_ok(), "Live AgentMemory must be healthy")

    def test_live_agentmemory_e2e_recall_opt_in(self) -> None:
        """Opt-in live AgentMemory end-to-end recall test (RUN_AGENTMEMORY_LIVE_TESTS=1). Fails if enabled and broken."""
        if os.environ.get("RUN_AGENTMEMORY_LIVE_TESTS") != "1":
            self.skipTest("Live AgentMemory tests not enabled. Set RUN_AGENTMEMORY_LIVE_TESTS=1 to run.")

        am = AgentMemoryProvider()
        health = am.check_health()
        if not health.is_ok():
            self.fail(f"Live AgentMemory server not available: {health.diagnostic}")

        res = am.recall_lessons_result("concurrency lock architecture", limit=3)
        self.assertIn(
            res.status,
            (ProviderStatus.OK, ProviderStatus.NO_RESULTS),
            f"Live AgentMemory query failed unexpectedly: {res.diagnostic}",
        )
        self.assertIsInstance(res.data, list)


if __name__ == "__main__":
    unittest.main()
