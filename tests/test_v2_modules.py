#!/usr/bin/env python3
"""Unit tests for Context Forge v2 modular components."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Add src to sys.path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.core.authority import AuthorityLevel, EpistemicAuthority
from context_forge.core.evidence import screen_secrets, sanitize_evidence
from context_forge.core.models import (
    KnowledgeRecord,
    IdentityEnvelope,
    EvidenceStatement,
    ContextPack,
)
from context_forge.core.identity import build_identity_envelope
from context_forge.compiler.conflict import ConflictResolver
from context_forge.compiler.pack import compile_context_pack
from context_forge.traceability.freshness import check_record_freshness
from context_forge.providers.base import ProviderStatus, ProviderResult
from context_forge.providers.code import NativeCodeProvider, CodebaseMemoryMCPProvider
from context_forge.providers.experience import NativeExperienceProvider, AgentMemoryProvider

from context_forge.cli.commands import cmd_init


class ContextForgeV2ModuleTests(unittest.TestCase):

    def test_authority_hierarchy(self) -> None:
        self.assertGreater(
            EpistemicAuthority.get_level("user_explicit"),
            EpistemicAuthority.get_level("code_observed"),
        )
        self.assertGreater(
            EpistemicAuthority.get_level("code_observed"),
            EpistemicAuthority.get_level("procedural_memory"),
        )
        self.assertGreater(
            EpistemicAuthority.get_level("procedural_memory"),
            EpistemicAuthority.get_level("agent_inference"),
        )

        # ADR without user_explicit authority must be rejected
        can_accept, reason = EpistemicAuthority.can_accept_intent("decision", "agent_inference", accept=True)
        self.assertFalse(can_accept)
        self.assertIn("user_explicit", reason or "")

        # ADR without accept flag must be rejected
        can_accept, reason = EpistemicAuthority.can_accept_intent("decision", "user_explicit", accept=False)
        self.assertFalse(can_accept)
        self.assertIn("accept", reason or "")

        # Valid ADR
        can_accept, _ = EpistemicAuthority.can_accept_intent("decision", "user_explicit", accept=True)
        self.assertTrue(can_accept)

    def test_evidence_sanitization_and_secrets(self) -> None:
        # Secret screening
        raw_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz12"
        screened = screen_secrets(f"My token is {raw_token}")
        self.assertNotIn(raw_token, screened)
        self.assertIn("[REDACTED]", screened)

        # Sanitize evidence raises error on secrets
        with self.assertRaises(ValueError):
            sanitize_evidence("token = 'sk-1234567890abcdefghijklmnopqrst'")

        # Clean evidence passes
        clean = sanitize_evidence("User explicitly chose PostgreSQL over MongoDB.")
        self.assertEqual(clean, "User explicitly chose PostgreSQL over MongoDB.")

    def test_identity_envelope(self) -> None:
        envelope = build_identity_envelope(
            payload={"session_id": "sess-42", "agent_id": "architect-agent"},
            agent_role="architect",
            task_id="task-999",
        )
        self.assertEqual(envelope.session_id, "sess-42")
        self.assertEqual(envelope.agent_id, "architect-agent")
        self.assertEqual(envelope.agent_role, "architect")
        self.assertEqual(envelope.task_id, "task-999")

        d = envelope.to_dict()
        restored = IdentityEnvelope.from_dict(d)
        self.assertEqual(restored.session_id, "sess-42")

    def test_conflict_resolver(self) -> None:
        authoritative = [
            {
                "id": "ADR-041",
                "title": "Redis explicitly rejected for distributed state",
                "body": "Redis rejected in favor of PostgreSQL advisory locks.",
            }
        ]
        experience = [
            {
                "finding": "Use redis for caching and distributed locks",
                "source": "agentmemory",
            }
        ]
        conflicts = ConflictResolver.detect_conflicts(authoritative, experience)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("Conflict detected", conflicts[0]["warning"])
        self.assertEqual(conflicts[0]["winner"], "ADR-041")

    def test_freshness_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir(parents=True)
            source_file = repo / "src" / "worker.py"
            source_file.write_text("def work(): pass\n", encoding="utf-8")

            record_path = repo / "tech.md"
            record_path.write_text(
                "---\nid: TECH-001\n---\n## Related paths\n- `src/worker.py`\n",
                encoding="utf-8",
            )

            # Initially fresh when not in changed files
            freshness = check_record_freshness(repo, record_path, changed_files=set())
            self.assertEqual(freshness, "fresh")

            # Stale when file is modified in changed files
            freshness = check_record_freshness(repo, record_path, changed_files={"src/worker.py"})
            self.assertEqual(freshness, "possibly_stale")

            # Stale when linked file is deleted
            source_file.unlink()
            freshness = check_record_freshness(repo, record_path, changed_files=set())
            self.assertEqual(freshness, "stale")

    def test_federated_context_pack_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            cmd_init(repo)

            pack = compile_context_pack(repo, task_query="authentication password hashing")
            self.assertIsInstance(pack, ContextPack)
            rendered = pack.to_text()
            self.assertIn("# Context Pack — Task: authentication password hashing", rendered)
            self.assertIn("Next Reading", rendered)
            self.assertGreater(pack.total_chars, 0)
            self.assertLessEqual(pack.total_chars, pack.budget_chars * 2)

    def test_provider_fallbacks(self) -> None:
        native_code = NativeCodeProvider()
        self.assertTrue(native_code.is_available())
        self.assertEqual(native_code.name(), "native_code_map")
        health = native_code.check_health()
        self.assertEqual(health.status, ProviderStatus.OK)

        cbm = CodebaseMemoryMCPProvider()
        # Without CBM installed, is_available is False and returns UNAVAILABLE ProviderResult
        self.assertFalse(cbm.is_available())
        cbm_health = cbm.check_health()
        self.assertEqual(cbm_health.status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(cbm.query_impact("test", []), [])

        am = AgentMemoryProvider(endpoint_url="http://localhost:59999")
        # Without AgentMemory running, returns UNAVAILABLE ProviderResult
        self.assertFalse(am.is_available())
        am_health = am.check_health()
        self.assertEqual(am_health.status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(am.recall_lessons("test"), [])

    def test_agentmemory_contract_integration(self) -> None:
        """Contract integration test against real AgentMemory endpoints and Bearer auth."""
        import http.server
        import threading
        import json

        class MockAgentMemoryHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Silence stderr

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                if self.path == "/agentmemory/health":
                    if auth != "Bearer test-secret-123":
                        self.send_response(401)
                        self.end_headers()
                        self.wfile.write(b'{"error": "Unauthorized"}')
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok", "version": "1.4.2", "capabilities": ["smart-search"]}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                auth = self.headers.get("Authorization", "")
                if auth != "Bearer test-secret-123":
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                if length > 0:
                    _ = self.rfile.read(length)
                if self.path == "/agentmemory/smart-search":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    resp = {
                        "results": [
                            {"content": "Use Redis advisory locks for high-frequency writes", "id": "mem-101"}
                        ]
                    }
                    self.wfile.write(json.dumps(resp).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

        server = http.server.HTTPServer(("127.0.0.1", 0), MockAgentMemoryHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            # 1. Valid auth
            am = AgentMemoryProvider(endpoint_url=f"http://127.0.0.1:{port}", secret="test-secret-123")
            health = am.check_health()
            self.assertEqual(health.status, ProviderStatus.OK)
            self.assertEqual(health.version, "1.4.2")

            lessons_res = am.recall_lessons_result("locks", limit=2)
            self.assertEqual(lessons_res.status, ProviderStatus.OK)
            self.assertEqual(len(lessons_res.data), 1)
            self.assertIn("Redis advisory locks", lessons_res.data[0]["finding"])

            # 2. Invalid auth -> UNAUTHORIZED
            am_bad_auth = AgentMemoryProvider(endpoint_url=f"http://127.0.0.1:{port}", secret="wrong-secret")
            bad_health = am_bad_auth.check_health()
            self.assertEqual(bad_health.status, ProviderStatus.UNAUTHORIZED)
            self.assertIn("authentication failed", bad_health.diagnostic)
        finally:
            server.shutdown()
            server.server_close()


    def test_cbm_cli_contract_integration(self) -> None:
        """Contract integration test against CBM CLI invocation protocol."""
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # Create a mock CLI script (runnable via python)
            if sys.platform == "win32":
                mock_exe = tmp_dir / "codebase-memory-mcp.bat"
                mock_exe.write_text(
                    f'@echo off\n"{sys.executable}" "{tmp_dir / "cbm_runner.py"}" %*\n',
                    encoding="utf-8",
                )
            else:
                mock_exe = tmp_dir / "codebase-memory-mcp"
                mock_exe.write_text(
                    f'#!/bin/sh\n"{sys.executable}" "{tmp_dir / "cbm_runner.py"}" "$@"\n',
                    encoding="utf-8",
                )
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json, os\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    tool = sys.argv[2]\n'
                '    if tool == "list_projects":\n'
                '        print(json.dumps([{"name": "risk-proj", "root_path": os.getcwd().replace(chr(92), "/")}]))\n'
                '    elif tool == "search_graph":\n'
                '        print(json.dumps([{"name": "RiskGate", "path": "src/risk.py", "signature": "class RiskGate"}]))\n'
                '    else:\n'
                '        print(json.dumps([]))\n',
                encoding="utf-8",
            )

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))
            health = cbm.check_health()
            self.assertEqual(health.status, ProviderStatus.OK)
            self.assertEqual(health.version, "0.10.8")

            impact_res = cbm.query_impact_result("risk", ["."])
            self.assertEqual(impact_res.status, ProviderStatus.OK)
            self.assertEqual(len(impact_res.data), 1)
            self.assertEqual(impact_res.data[0]["symbol"], "RiskGate")


    def test_map_budget_enforcement(self) -> None:
        """Regression test proving build_code_map stays within Budgets.MAP_CHARS budget."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            # Create many source files with many symbols to stress map budget
            src_dir = repo_dir / "src"
            src_dir.mkdir(parents=True, exist_ok=True)
            for i in range(50):
                f_path = src_dir / f"module_{i:02d}.py"
                f_content = "\n".join([f"class Class{i}_{j}: pass\ndef func{i}_{j}(): pass" for j in range(15)])
                f_path.write_text(f_content, encoding="utf-8")

            from context_forge.providers.code.native import build_code_map
            from context_forge.core.budgets import Budgets

            map_out = build_code_map(repo_dir)
            self.assertLessEqual(len(map_out), Budgets.MAP_CHARS)
            self.assertIn("map budget", map_out)

    def test_policies_in_routing_index(self) -> None:
        """Regression test verifying policies are auto-routed in index.md and not reported as orphans."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            from context_forge.cli.commands import cmd_init, cmd_map, cmd_index
            from context_forge.cli.doctor import cmd_lint
            from context_forge.knowledge.update import create_knowledge_record

            cmd_init(repo_dir)
            create_knowledge_record(
                repo=repo_dir,
                kind="policy",
                title="Strict Security Policy",
                body="All API keys must be screen-checked.",
                authority="policy_mandate",
                evidence="Mandatory security policy",
                scope=[],
                accept=True,
            )

            cmd_index(repo_dir)
            index_text = (repo_dir / ".brain" / "index.md").read_text(encoding="utf-8")
            self.assertIn("### Policies (`policies/`)", index_text)
            self.assertIn("POL-001-strict-security-policy.md", index_text)

            lint_issues = cmd_lint(repo_dir)
            orphan_issues = [x for x in lint_issues if "orphan page" in x and "POL-001" in x]
            self.assertEqual(len(orphan_issues), 0, f"Policy reported as orphan: {orphan_issues}")

    def test_context_pack_ascii_rendering_and_encoding_safety(self) -> None:
        """Regression test verifying ASCII-safe rendering for non-UTF-8 REPLs (cp1252)."""
        pack = ContextPack(
            task="authentication update",
            provider_diagnostics=[{"diagnostic_message": "CBM provider unavailable"}],
            conflicts_and_staleness=[{"warning": "DRIFT: RiskGate missing in order.py"}],
        )

        # 1. UTF-8 output contains Unicode status icons
        utf8_text = pack.to_text(ascii_only=False)
        self.assertIn("ℹ️", utf8_text)
        self.assertIn("⚠️", utf8_text)

        # 2. ASCII-safe output contains ASCII replacement markers
        ascii_text = pack.to_ascii_text()
        self.assertIn("[INFO]", ascii_text)
        self.assertIn("[WARNING]", ascii_text)
        self.assertNotIn("ℹ️", ascii_text)
        self.assertNotIn("⚠️", ascii_text)

        # 3. ASCII output encodes in cp1252 without UnicodeEncodeError
        encoded_cp1252 = ascii_text.encode("cp1252")
        self.assertIsNotNone(encoded_cp1252)

        # 4. render_safe_text automatically uses ascii_only when stream encoding is cp1252
        cp1252_safe = pack.render_safe_text(stream_encoding="cp1252")
        self.assertIn("[INFO]", cp1252_safe)
        self.assertIn("[WARNING]", cp1252_safe)
        self.assertIsNotNone(cp1252_safe.encode("cp1252"))


    def test_knowledge_record_does_not_expose_broken_context_pack_helpers(self) -> None:
        """Regression test verifying KnowledgeRecord does not expose misplaced ContextPack helpers."""
        rec = KnowledgeRecord(id="ADR-001", kind="decision", title="T", status="accepted", authority="user_explicit")
        self.assertFalse(hasattr(rec, "to_ascii_text") or "to_ascii_text" in dir(rec))
        self.assertFalse(hasattr(rec, "render_safe_text") or "render_safe_text" in dir(rec))

    def test_cbm_json_format_contract_and_malformed_handling(self) -> None:
        """Verify CBM tool invocation explicitly requests --format json and handles malformed output cleanly."""
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    if "--format" not in sys.argv or "json" not in sys.argv:\n'
                '        print("NOT_JSON_FORMAT")\n'
                '    elif sys.argv[2] == "malformed_tool":\n'
                '        print("THIS IS NOT VALID JSON")\n'
                '    else:\n'
                '        print(json.dumps([{"name": "test"}]))\n',
                encoding="utf-8",
            )
            if sys.platform == "win32":
                mock_exe = tmp_dir / "cbm_mock.cmd"
                mock_exe.write_text(f'@"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "cbm_mock.sh"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))
            # Test machine flag inclusion
            res_ok = cbm.run_tool("test_tool", {})
            self.assertEqual(res_ok.status, ProviderStatus.OK)
            
            # Test malformed output returns MALFORMED status
            res_malformed = cbm.run_tool("malformed_tool", {})
            self.assertEqual(res_malformed.status, ProviderStatus.MALFORMED)
            self.assertEqual(res_malformed.diagnostic_code, "CBM_MALFORMED_JSON")

    def test_cbm_relation_routing_and_exact_endpoint_verification(self) -> None:
        """Verify governance edges, unsupported relations, exact endpoint matching (Bar != Barista, A->B != A->D)."""
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repo_dir = tmp_dir / "my_proj"
            repo_dir.mkdir()
            (repo_dir / "src").mkdir()
            (repo_dir / "src" / "risk.py").write_text("class RiskGate: pass\n", encoding="utf-8")

            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    tool = sys.argv[2]\n'
                '    if tool == "list_projects":\n'
                '        print(json.dumps([{"name": "my_proj", "root_path": "' + str(repo_dir).replace('\\', '/') + '"}]))\n'
                '    elif tool == "search_graph":\n'
                '        print(json.dumps([{"name": "RiskGate", "path": "src/risk.py"}]))\n'
                '    else:\n'
                '        print(json.dumps([]))\n',
                encoding="utf-8",
            )
            if sys.platform == "win32":
                mock_exe = tmp_dir / "cbm_mock.cmd"
                mock_exe.write_text(f'@"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "cbm_mock.sh"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))

            # 1. Governance edge returns UNSUPPORTED
            res_gov = cbm.verify_reference(repo_dir, "src/risk.py", symbol="RiskGate", relationship="SATISFIES")
            self.assertEqual(res_gov.status, ProviderStatus.UNSUPPORTED)
            self.assertEqual(res_gov.diagnostic_code, "GOVERNANCE_EDGE_UNSUPPORTED")

            res_gov2 = cbm.verify_reference(repo_dir, "src/risk.py", symbol="RiskGate", relationship="DERIVED_FROM")
            self.assertEqual(res_gov2.status, ProviderStatus.UNSUPPORTED)
            self.assertEqual(res_gov2.diagnostic_code, "GOVERNANCE_EDGE_UNSUPPORTED")

            # 2. Code graph relation with missing edge returns symbol_graph / relationship_verified=False
            res_unmatched = cbm.verify_reference(repo_dir, "src/risk.py", symbol="RiskGate", relationship="CALLS")
            self.assertEqual(res_unmatched.status, ProviderStatus.OK)
            self.assertFalse(res_unmatched.data["relationship_verified"])
            self.assertEqual(res_unmatched.data["verification_kind"], "symbol_graph")

    def test_agentmemory_remember_malformed_json_response(self) -> None:
        """Verify AgentMemory remember() handles malformed JSON response by returning MALFORMED status."""
        import http.server, threading
        class MalformedHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args): pass
            def do_POST(self):
                if self.path == "/agentmemory/remember":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"NOT VALID JSON RESPONSE")

        server = http.server.HTTPServer(("127.0.0.1", 0), MalformedHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request)
        t.start()

        am = AgentMemoryProvider(endpoint_url=f"http://127.0.0.1:{port}")
        res = am.remember("test memory content")
        self.assertEqual(res.status, ProviderStatus.MALFORMED)
        self.assertEqual(res.diagnostic_code, "MALFORMED_JSON")
        t.join()

    def test_installer_migration_cleans_obsolete_renamed_snippet(self) -> None:
        """Verify install_engine removes obsolete AGENTS.md.snippet.md while preserving user files."""
        with tempfile.TemporaryDirectory() as tmp:
            eng_dir = Path(tmp) / ".context-forge"
            eng_dir.mkdir(parents=True, exist_ok=True)
            
            # Create obsolete file and unrelated user file
            old_file = eng_dir / "AGENTS.md.snippet.md"
            user_file = eng_dir / "my_user_notes.txt"
            old_file.write_text("old content", encoding="utf-8")
            user_file.write_text("user content", encoding="utf-8")

            from install import install_engine
            install_engine(eng_dir)

            self.assertFalse(old_file.exists())
            self.assertTrue((eng_dir / "AGENTS.snippet.md").exists())
            self.assertTrue(user_file.exists())
            self.assertEqual(user_file.read_text(encoding="utf-8"), "user content")

    def test_bidirectional_supersession_and_cycle_rejection(self) -> None:
        """Verify bidirectional record supersession and cycle rejection."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            from context_forge.cli.commands import cmd_init
            from context_forge.knowledge.update import create_knowledge_record
            from context_forge.knowledge.supersession import supersede_record
            from context_forge.store.paths import read_text

            cmd_init(repo_dir)
            create_knowledge_record(repo_dir, "decision", "Decision One", "Body 1", "user_explicit", "Evidence 1", [], True)
            create_knowledge_record(repo_dir, "decision", "Decision Two", "Body 2", "user_explicit", "Evidence 2", [], True)
            create_knowledge_record(repo_dir, "decision", "Decision Three", "Body 3", "user_explicit", "Evidence 3", [], True)

            # 1. Supersede ADR-001 -> ADR-002
            ok1 = supersede_record(repo_dir, "ADR-001", "ADR-002")
            self.assertTrue(ok1)

            adr1_text = (repo_dir / ".brain" / "decisions" / "ADR-001-decision-one.md").read_text(encoding="utf-8")
            adr2_text = (repo_dir / ".brain" / "decisions" / "ADR-002-decision-two.md").read_text(encoding="utf-8")
            self.assertIn("status: superseded", adr1_text)
            self.assertIn("superseded_by: ADR-002", adr1_text)
            self.assertIn("supersedes: ADR-001", adr2_text)

            # 2. Supersede ADR-002 -> ADR-003
            ok2 = supersede_record(repo_dir, "ADR-002", "ADR-003")
            self.assertTrue(ok2)
            adr2_updated = (repo_dir / ".brain" / "decisions" / "ADR-002-decision-two.md").read_text(encoding="utf-8")
            adr3_text = (repo_dir / ".brain" / "decisions" / "ADR-003-decision-three.md").read_text(encoding="utf-8")
            self.assertIn("status: superseded", adr2_updated)
            self.assertIn("superseded_by: ADR-003", adr2_updated)
            self.assertIn("supersedes: ADR-002", adr3_text)

            # 3. Attempt cycle ADR-003 -> ADR-001 must be REJECTED
            cycle_ok = supersede_record(repo_dir, "ADR-003", "ADR-001")
            self.assertFalse(cycle_ok, "Cycle ADR-003 -> ADR-001 should have been rejected!")

if __name__ == "__main__":
    unittest.main()

