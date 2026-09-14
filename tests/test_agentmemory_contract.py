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
                elif self.path == "/agentmemory/remember":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    content = body.get("content", "")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "saved", "id": "mem-new-123", "content": content}).encode("utf-8"))
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

            # 3. Empty JSON -> INCOMPATIBLE
            am_empty = AgentMemoryProvider(endpoint_url=url, secret="secret-empty-json")
            h_emp = am_empty.check_health()
            self.assertEqual(h_emp.status, ProviderStatus.INCOMPATIBLE)
            self.assertIn("lacks AgentMemory identity", h_emp.diagnostic)

            # 4. Arbitrary non-AgentMemory JSON -> INCOMPATIBLE
            am_arb = AgentMemoryProvider(endpoint_url=url, secret="secret-arbitrary-json")
            h_arb = am_arb.check_health()
            self.assertEqual(h_arb.status, ProviderStatus.INCOMPATIBLE)

            # 5. Missing capabilities -> INCOMPATIBLE
            am_st = AgentMemoryProvider(endpoint_url=url, secret="secret-status-ok")
            h_st = am_st.check_health()
            self.assertEqual(h_st.status, ProviderStatus.INCOMPATIBLE)

            # 6. Version only without capabilities/service -> INCOMPATIBLE
            am_vo = AgentMemoryProvider(endpoint_url=url, secret="secret-version-only")
            h_vo = am_vo.check_health()
            self.assertEqual(h_vo.status, ProviderStatus.INCOMPATIBLE)

            # 7. Degraded state
            am_deg = AgentMemoryProvider(endpoint_url=url, secret="secret-degraded")
            h_deg = am_deg.check_health()
            self.assertEqual(h_deg.status, ProviderStatus.DEGRADED)
            self.assertEqual(h_deg.version, "1.4.2")

            # 8. Server error 500 -> ERROR
            am_err = AgentMemoryProvider(endpoint_url=url, secret="secret-server-error")
            h_err = am_err.check_health()
            self.assertEqual(h_err.status, ProviderStatus.ERROR)

            # 9. Server 503 with service identity -> DEGRADED
            am_503 = AgentMemoryProvider(endpoint_url=url, secret="secret-503-am")
            h_503 = am_503.check_health()
            self.assertEqual(h_503.status, ProviderStatus.DEGRADED)

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

            # 13. Write / remember
            w_res = am_valid.remember("Always enforce locks", concepts=["locks"])
            self.assertEqual(w_res.status, ProviderStatus.OK)
            self.assertEqual(w_res.data["status"], "saved")

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
        """Opt-in live AgentMemory end-to-end write->recall test (RUN_AGENTMEMORY_LIVE_TESTS=1). Fails if enabled and broken."""
        if os.environ.get("RUN_AGENTMEMORY_LIVE_TESTS") != "1":
            self.skipTest("Live AgentMemory tests not enabled. Set RUN_AGENTMEMORY_LIVE_TESTS=1 to run.")

        import tempfile
        import uuid
        from context_forge.cli.commands import cmd_init

        am = AgentMemoryProvider()
        health = am.check_health()
        if not health.is_ok():
            self.fail(f"Live AgentMemory server not available: {health.diagnostic}")

        probe_id = f"context-forge-live-probe-{uuid.uuid4().hex[:12]}"
        probe_content = f"Test architectural experience probe: {probe_id}. Always enforce isolated thread locks."

        # Write probe memory via upstream API
        write_res = am.remember(
            content=probe_content,
            concepts=["test-probe", "concurrency"],
            metadata={"ephemeral": True, "producer": "context-forge-e2e-test", "probe_id": probe_id},
        )
        if not write_res.is_ok():
            self.fail(f"Live AgentMemory write failed for probe {probe_id}: {write_res.diagnostic}")

        # Recall probe memory via smart-search
        recall_res = am.recall_lessons_result(probe_id, limit=3)
        if recall_res.status == ProviderStatus.NO_RESULTS:
            self.fail(f"Live AgentMemory probe recall returned NO_RESULTS for newly written probe: {probe_id}")
        if not recall_res.is_ok():
            self.fail(f"Live AgentMemory probe recall failed: {recall_res.diagnostic}")

        found = any(probe_id in str(item.get("finding", "")) or probe_id in str(item.get("lesson", "")) for item in recall_res.data)
        if not found:
            self.fail(f"Live AgentMemory probe recall results did not contain probe {probe_id}: {recall_res.data}")

        # Compile into ContextPack and verify it appears in Past Experience
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cmd_init(repo)
            from context_forge.compiler.pack import compile_context_pack
            pack = compile_context_pack(repo, task_query=probe_id, paths=[])
            self.assertIsNotNone(pack)
            pack_text = pack.to_text()
            self.assertIn("Past Experience", pack_text)
            self.assertIn(probe_id, pack_text)


if __name__ == "__main__":
    unittest.main()
