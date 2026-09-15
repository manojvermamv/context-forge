import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.providers.base import ProviderStatus
from context_forge.providers.code.cbm import CodebaseMemoryMCPProvider, normalize_repo_path


class TestCBMContract(unittest.TestCase):
    def test_binary_missing(self) -> None:
        cbm = CodebaseMemoryMCPProvider(executable_path="/nonexistent/path/to/cbm")
        self.assertFalse(cbm.is_available())
        res = cbm.check_health()
        self.assertEqual(res.status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(res.diagnostic_code, "CBM_BINARY_NOT_FOUND")

    def test_mock_cbm_full_contract_flow(self) -> None:
        """Verify list_projects -> project resolution -> search_graph with project identity."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repo_dir = tmp_dir / "my_project"
            repo_dir.mkdir()

            # Create mock CBM executable
            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    tool = sys.argv[2]\n'
                '    args = {}\n'
                '    i = 3\n'
                '    while i < len(sys.argv):\n'
                '        arg = sys.argv[i]\n'
                '        if arg.startswith("--"):\n'
                '            k = arg[2:].replace("-", "_")\n'
                '            if i + 1 < len(sys.argv) and not sys.argv[i+1].startswith("--"):\n'
                '                v = sys.argv[i+1]\n'
                '                try: v = json.loads(v)\n'
                '                except Exception: pass\n'
                '                args[k] = v\n'
                '                i += 2\n'
                '            else:\n'
                '                args[k] = True\n'
                '                i += 1\n'
                '        elif arg.startswith("{"):\n'
                '            try: args.update(json.loads(arg))\n'
                '            except Exception: pass\n'
                '            i += 1\n'
                '        else:\n'
                '            i += 1\n'
                '    if tool == "list_projects":\n'
                f'        print(json.dumps([{{\n'
                f'            "name": "my-project-id",\n'
                f'            "root_path": "{str(repo_dir.resolve()).replace(chr(92), "/")}",\n'
                f'        }}]))\n'
                '    elif tool == "search_graph":\n'
                '        assert args.get("project") == "my-project-id", "project arg required"\n'
                '        print(json.dumps([{"name": "AuthService", "path": "src/auth.py", "signature": "class AuthService"}]))\n'
                '    elif tool == "index_status":\n'
                '        print(json.dumps({"status": "ready"}))\n'
                '    elif tool == "index_repository":\n'
                '        print(json.dumps({"status": "indexed"}))\n'
                '    else:\n'
                '        print(json.dumps([]))\n',
                encoding="utf-8",
            )

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))

            # Health
            health = cbm.check_health()
            self.assertEqual(health.status, ProviderStatus.OK)
            self.assertEqual(health.version, "0.10.8")

            # Resolve project
            proj_name, err = cbm.resolve_project(repo_dir)
            self.assertIsNone(err)
            self.assertEqual(proj_name, "my-project-id")

            # Query impact passing repo_path explicitly, distinct from scope_paths
            impact_res = cbm.query_impact_result(repo_path=repo_dir, query="AuthService", scope_paths=["src/auth.py"])
            self.assertEqual(impact_res.status, ProviderStatus.OK)
            self.assertEqual(impact_res.project_name, "my-project-id")
            self.assertEqual(len(impact_res.data), 1)
            self.assertEqual(impact_res.data[0]["symbol"], "AuthService")

            # Verify verify_reference contract on CBM
            ref_res = cbm.verify_reference(repo_path=repo_dir, path="src/auth.py", symbol="AuthService")
            self.assertEqual(ref_res.status, ProviderStatus.OK)
            self.assertEqual(ref_res.data["symbol"], "AuthService")
            self.assertEqual(ref_res.data["verification_kind"], "symbol_graph")
            self.assertEqual(ref_res.data["confidence"], 0.90)
            self.assertFalse(ref_res.data["structurally_verified"])

    def test_relationship_verification_distinguishes_structural_vs_symbol(self) -> None:
        """Verify that relationship verification returns structural_graph only when relationship matches."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repo_dir = tmp_dir / "rel_project"
            repo_dir.mkdir()

            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    tool = sys.argv[2]\n'
                '    args = {}\n'
                '    i = 3\n'
                '    while i < len(sys.argv):\n'
                '        arg = sys.argv[i]\n'
                '        if arg.startswith("--"):\n'
                '            k = arg[2:].replace("-", "_")\n'
                '            if i + 1 < len(sys.argv) and not sys.argv[i+1].startswith("--"):\n'
                '                v = sys.argv[i+1]\n'
                '                try: v = json.loads(v)\n'
                '                except Exception: pass\n'
                '                args[k] = v\n'
                '                i += 2\n'
                '            else:\n'
                '                args[k] = True\n'
                '                i += 1\n'
                '        elif arg.startswith("{"):\n'
                '            try: args.update(json.loads(arg))\n'
                '            except Exception: pass\n'
                '            i += 1\n'
                '        else:\n'
                '            i += 1\n'
                '    if tool == "list_projects":\n'
                f'        print(json.dumps([{{\n'
                f'            "name": "rel-proj-id",\n'
                f'            "root_path": "{str(repo_dir.resolve()).replace(chr(92), "/")}",\n'
                f'        }}]))\n'
                '    elif tool == "search_graph":\n'
                '        q = args.get("query", "")\n'
                '        if "TokenService" in q:\n'
                '            print(json.dumps({\n'
                '                "nodes": [{"name": "TokenService", "path": "src/token.py"}],\n'
                '                "edges": [{"type": "implements", "source": "TokenService", "target": "IToken"}]\n'
                '            }))\n'
                '        else:\n'
                '            print(json.dumps([]))\n'
                '    else:\n'
                '        print(json.dumps([]))\n',
                encoding="utf-8",
            )

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))

            # Case A: relationship matches edge
            res_match = cbm.verify_reference(repo_path=repo_dir, path="src/token.py", symbol="TokenService", relationship="implements")
            self.assertEqual(res_match.status, ProviderStatus.OK)
            self.assertEqual(res_match.data["verification_kind"], "structural_graph")
            self.assertTrue(res_match.data["structurally_verified"])
            self.assertEqual(res_match.data["confidence"], 0.95)

            # Case B: symbol matches, but requested relationship is missing
            res_no_rel = cbm.verify_reference(repo_path=repo_dir, path="src/token.py", symbol="TokenService", relationship="calls_database")
            self.assertEqual(res_no_rel.status, ProviderStatus.OK)
            self.assertEqual(res_no_rel.data["verification_kind"], "symbol_graph")
            self.assertFalse(res_no_rel.data["structurally_verified"])
            self.assertEqual(res_no_rel.data["confidence"], 0.90)

            # Case C: adversarial - relationship matches edge type, but target_symbol does NOT match
            res_wrong_target = cbm.verify_reference(repo_path=repo_dir, path="src/token.py", symbol="TokenService", relationship="implements", target_symbol="IWrongInterface")
            self.assertEqual(res_wrong_target.status, ProviderStatus.OK)
            self.assertEqual(res_wrong_target.data["verification_kind"], "symbol_graph")
            self.assertFalse(res_wrong_target.data["structurally_verified"])
            self.assertFalse(res_wrong_target.data["relationship_verified"])

            # Case D: adversarial - unrelated symbol with matching relationship type must NOT verify
            res_unrelated = cbm.verify_reference(repo_path=repo_dir, path="src/other.py", symbol="OtherService", relationship="implements")
            self.assertFalse((res_unrelated.data or {}).get("structurally_verified", False))
            self.assertFalse((res_unrelated.data or {}).get("relationship_verified", False))

    def test_cbm_root_plumbing_distinct_from_scope_paths(self) -> None:
        """Adversarial test: repository root != cwd, scope_paths contains relative and absolute files."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repo_root = tmp_dir / "my_actual_repo"
            repo_root.mkdir()
            (repo_root / "src").mkdir()
            rel_file = "src/payment.py"
            abs_file = str((repo_root / "src" / "worker.py").resolve())

            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json, os\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    tool = sys.argv[2]\n'
                '    args = {}\n'
                '    i = 3\n'
                '    while i < len(sys.argv):\n'
                '        arg = sys.argv[i]\n'
                '        if arg.startswith("--"):\n'
                '            k = arg[2:].replace("-", "_")\n'
                '            if i + 1 < len(sys.argv) and not sys.argv[i+1].startswith("--"):\n'
                '                v = sys.argv[i+1]\n'
                '                try: v = json.loads(v)\n'
                '                except Exception: pass\n'
                '                args[k] = v\n'
                '                i += 2\n'
                '            else:\n'
                '                args[k] = True\n'
                '                i += 1\n'
                '        elif arg.startswith("{"):\n'
                '            try: args.update(json.loads(arg))\n'
                '            except Exception: pass\n'
                '            i += 1\n'
                '        else:\n'
                '            i += 1\n'
                '    if tool == "list_projects":\n'
                f'        print(json.dumps([{{\n'
                f'            "name": "my-actual-repo-id",\n'
                f'            "root_path": "{str(repo_root.resolve()).replace(chr(92), "/")}",\n'
                f'        }}]))\n'
                '    elif tool == "search_graph":\n'
                '        assert args.get("project") == "my-actual-repo-id", f"Expected my-actual-repo-id, got {args.get(\'project\')}"\n'
                '        print(json.dumps([{"name": "PaymentEngine", "path": "src/payment.py", "signature": "class PaymentEngine"}]))\n'
                '    else:\n'
                '        print(json.dumps([]))\n',
                encoding="utf-8",
            )

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))

            # Case A: scope_paths contains relative and absolute files, repo_root is distinct
            res_a = cbm.query_impact_result(
                repo_path=repo_root,
                query="PaymentEngine",
                scope_paths=[rel_file, abs_file],
            )
            self.assertEqual(res_a.status, ProviderStatus.OK)
            self.assertEqual(res_a.project_name, "my-actual-repo-id")

            # Case B: scope_paths is empty
            res_b = cbm.query_impact_result(
                repo_path=repo_root,
                query="PaymentEngine",
                scope_paths=[],
            )
            self.assertEqual(res_b.status, ProviderStatus.OK)

            # Case C: verify_reference on repo_root
            ref_res = cbm.verify_reference(repo_path=repo_root, path="src/payment.py", symbol="PaymentEngine")
            self.assertEqual(ref_res.status, ProviderStatus.OK)

    def test_unindexed_repo_returns_unindexed_status(self) -> None:
        """Verify unindexed repo returns UNINDEXED without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            repo_dir = tmp_dir / "unindexed_project"
            repo_dir.mkdir()

            runner = tmp_dir / "cbm_runner.py"
            runner.write_text(
                'import sys, json\n'
                'if "--version" in sys.argv:\n'
                '    print("0.10.8")\n'
                'elif len(sys.argv) >= 3 and sys.argv[1] == "cli":\n'
                '    print(json.dumps([]))\n',  # Empty projects
                encoding="utf-8",
            )

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe), auto_index=False)
            proj_name, err = cbm.resolve_project(repo_dir)
            self.assertIsNone(proj_name)
            self.assertIsNotNone(err)
            self.assertEqual(err.status, ProviderStatus.UNINDEXED)

    def test_tool_nonzero_exit_returns_error(self) -> None:
        """Verify tool crash surfaces as ProviderStatus.ERROR."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            runner = tmp_dir / "cbm_runner.py"
            runner.write_text('import sys\nsys.stderr.write("Fatal crash\\n")\nsys.exit(2)\n', encoding="utf-8")

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))
            res = cbm.run_tool("list_projects", {})
            self.assertEqual(res.status, ProviderStatus.ERROR)
            self.assertIn("Fatal crash", res.diagnostic)

    def test_malformed_json_returns_malformed_status(self) -> None:
        """Verify tool returning exit 0 with non-JSON output surfaces as ProviderStatus.MALFORMED."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            runner = tmp_dir / "cbm_runner.py"
            runner.write_text('import sys\nprint("Not valid json at all")\n', encoding="utf-8")

            if sys.platform == "win32":
                mock_exe = tmp_dir / "mock_cbm.bat"
                mock_exe.write_text(f'@echo off\n"{sys.executable}" "{runner}" %*\n', encoding="utf-8")
            else:
                mock_exe = tmp_dir / "mock_cbm"
                mock_exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{runner}" "$@"\n', encoding="utf-8")
                mock_exe.chmod(mock_exe.stat().st_mode | stat.S_IEXEC)

            cbm = CodebaseMemoryMCPProvider(executable_path=str(mock_exe))
            res = cbm.run_tool("list_projects", {})
            self.assertEqual(res.status, ProviderStatus.MALFORMED)
            self.assertEqual(res.diagnostic_code, "CBM_MALFORMED_JSON")

    def test_live_cbm_health_opt_in(self) -> None:
        """Opt-in live CBM health test (RUN_CBM_LIVE_TESTS=1). Fails if enabled and unhealthy."""
        if os.environ.get("RUN_CBM_LIVE_TESTS") != "1":
            self.skipTest("Live CBM tests not enabled. Set RUN_CBM_LIVE_TESTS=1 to run.")

        cbm = CodebaseMemoryMCPProvider()
        health = cbm.check_health()
        if not health.is_ok():
            self.fail(f"Live CBM binary not available or healthy: {health.diagnostic}")

        self.assertTrue(health.version != "", "Live CBM must report a version")

    def test_live_cbm_e2e_query_opt_in(self) -> None:
        """Opt-in live CBM end-to-end structural query test (RUN_CBM_LIVE_TESTS=1). Fails if enabled and broken."""
        if os.environ.get("RUN_CBM_LIVE_TESTS") != "1":
            self.skipTest("Live CBM tests not enabled. Set RUN_CBM_LIVE_TESTS=1 to run.")

        cbm = CodebaseMemoryMCPProvider(auto_index=True)
        health = cbm.check_health()
        if not health.is_ok():
            self.fail(f"Live CBM binary not available: {health.diagnostic}")

        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=fixture_repo, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=fixture_repo, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=fixture_repo, capture_output=True, check=True)

            code_file = fixture_repo / "payment.py"
            code_file.write_text(
                "class PaymentGateway:\n"
                "    def process_payment(self, amount: float) -> bool:\n"
                "        raise NotImplementedError\n\n"
                "class StripeGateway(PaymentGateway):\n"
                "    def process_payment(self, amount: float) -> bool:\n"
                "        return True\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "payment.py"], cwd=fixture_repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=fixture_repo, capture_output=True, check=True)

            proj_name, err = cbm.resolve_project(fixture_repo, allow_index=True)
            if err or not proj_name:
                self.fail(f"Live CBM failed to index and resolve fixture project: {err.diagnostic if err else 'unknown'}")

            res = cbm.verify_reference(
                repo_path=fixture_repo,
                path="payment.py",
                symbol="StripeGateway",
                relationship="inherits",
                target_symbol="PaymentGateway",
            )
            if res.status == ProviderStatus.UNINDEXED:
                self.fail("Live CBM E2E cannot pass with UNINDEXED status")
            if res.status == ProviderStatus.NO_RESULTS:
                self.fail("Live CBM E2E probe failed: known symbol 'StripeGateway' returned NO_RESULTS")
            if not res.is_ok():
                self.fail(f"Live CBM verify_reference failed: {res.diagnostic}")

            self.assertTrue(res.data.get("relationship_requested"), "relationship_requested must be True")
            self.assertTrue(res.data.get("relationship_verified"), "relationship_verified must be True")
            self.assertTrue(res.data.get("structurally_verified"), "structurally_verified must be True")
            self.assertEqual(res.data.get("verification_kind"), VerificationKind.STRUCTURAL_GRAPH.value)

            from context_forge.compiler.pack import compile_context_pack
            pack = compile_context_pack(fixture_repo, task_query="Payment processing", paths=["payment.py"])
            self.assertIsNotNone(pack)


if __name__ == "__main__":
    unittest.main()
