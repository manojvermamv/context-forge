from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import cmd_init
from context_forge.knowledge.update import create_knowledge_record
from context_forge.traceability.resolver import (
    TraceEdgeType,
    is_test_path,
    reconcile_traceability_with_provider,
    resolve_traceability_graph,
)
from context_forge.providers.base import ProviderResult, ProviderStatus


class TraceabilityGraphTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cmd_init(self.tmp)
        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        (self.tmp / "tests").mkdir(parents=True, exist_ok=True)
        (self.tmp / "src" / "router.py").write_text("class Router: pass\n", encoding="utf-8")
        (self.tmp / "tests" / "test_router.py").write_text("def test_route(): pass\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_test_path_detection_robustness(self):
        """Path detection distinguishes real test files from files with 'test' in the name."""
        self.assertTrue(is_test_path("tests/test_router.py"))
        self.assertTrue(is_test_path("tests/router_test.py"))
        self.assertTrue(is_test_path("src/tests/unit.py"))
        self.assertTrue(is_test_path("specs/service.spec.ts"))

        # Non-test paths that happen to contain the substring 'test'
        self.assertFalse(is_test_path("src/testing_utilities.py"))
        self.assertFalse(is_test_path("src/contest_winner.py"))
        self.assertFalse(is_test_path("src/protester.py"))

    def test_typed_traceability_graph_generation(self):
        """Traceability resolution produces explicit typed edges for implementation vs tests."""
        create_knowledge_record(
            repo=self.tmp,
            kind="requirement",
            title="Route Dispatcher",
            body="Router must dispatch paths.",
            authority="user_explicit",
            evidence="Product specs",
            scope=["src/router.py", "tests/test_router.py", "src/missing_component.py"],
            accept=True,
        )

        graph = resolve_traceability_graph(self.tmp)
        self.assertEqual(len(graph), 1)
        entry = graph[0]

        edges = entry["edges"]
        self.assertEqual(len(edges), 3)

        impl_edges = [e for e in edges if e["edge_type"] == TraceEdgeType.IMPLEMENTS.value]
        verify_edges = [e for e in edges if e["edge_type"] == TraceEdgeType.VERIFIES_WITH.value]

        self.assertEqual(len(impl_edges), 2)
        self.assertEqual(len(verify_edges), 1)
        self.assertEqual(verify_edges[0]["target_kind"], "test")

        # Check existing file vs missing file
        existing_impl = [e for e in impl_edges if e["target_ref"] == "src/router.py"][0]
        self.assertEqual(existing_impl["verification_state"], "unverified")

        missing_impl = [e for e in impl_edges if e["target_ref"] == "src/missing_component.py"][0]
        self.assertEqual(missing_impl["verification_state"], "stale")

    def test_provider_reconciliation_with_verify_reference(self):
        """When provider confirms structural reference, edge confidence is upgraded to verified."""
        create_knowledge_record(
            repo=self.tmp,
            kind="decision",
            title="Routing Architecture",
            body="Architecture for router.",
            authority="user_explicit",
            evidence="Design doc",
            scope=["src/router.py"],
            accept=True,
        )

        graph = resolve_traceability_graph(self.tmp)

        class MockCBMProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.OK,
                    data={
                        "symbol": "Router",
                        "path": path,
                        "structurally_verified": True,
                        "verification_kind": "structural_graph",
                        "confidence": 0.95,
                    },
                    provider="cbm",
                    diagnostic="Verified in CBM graph.",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockCBMProvider())
        edge = reconciled[0]["edges"][0]
        self.assertEqual(edge["provider"], "cbm")
        self.assertEqual(edge["verification_state"], "verified")
        self.assertGreaterEqual(edge["confidence"], 0.9)
        self.assertIn("Verified in CBM graph", edge["evidence"])

    def test_provider_reconciliation_missing_reference_marks_stale(self):
        """When provider reports entity missing (NO_RESULTS), edge is marked stale."""
        create_knowledge_record(
            repo=self.tmp,
            kind="decision",
            title="Old Router",
            body="Old router doc.",
            authority="user_explicit",
            evidence="Design doc",
            scope=["src/router.py"],
            accept=True,
        )
        graph = resolve_traceability_graph(self.tmp)

        class MockMissingProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.NO_RESULTS,
                    provider="cbm",
                    diagnostic="Symbol or file not found in project graph.",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockMissingProvider())
        edge = reconciled[0]["edges"][0]
        self.assertEqual(edge["verification_state"], "stale")
        self.assertEqual(edge["confidence"], 0.0)

    def test_provider_reconciliation_unavailable_preserves_historical_edge(self):
        """When provider is unavailable, historical edge is preserved as unverified without hallucination."""
        create_knowledge_record(
            repo=self.tmp,
            kind="decision",
            title="Router System",
            body="Architecture for router.",
            authority="user_explicit",
            evidence="Design doc",
            scope=["src/router.py"],
            accept=True,
        )
        graph = resolve_traceability_graph(self.tmp)

        class MockUnavailableProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.UNAVAILABLE,
                    provider="cbm",
                    diagnostic="Binary not found.",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockUnavailableProvider())
        edge = reconciled[0]["edges"][0]
        self.assertEqual(edge["verification_state"], "unverified")
        self.assertIn("unavailable", edge["evidence"])

    def test_provider_interface_violation_raises_explicit_error(self):
        """Provider missing verify_reference method must raise TypeError, NOT be silently swallowed!"""
        create_knowledge_record(
            repo=self.tmp,
            kind="decision",
            title="Router",
            body="Router decision.",
            authority="user_explicit",
            evidence="Design doc",
            scope=["src/router.py"],
            accept=True,
        )
        graph = resolve_traceability_graph(self.tmp)

        class BrokenProvider:
            def is_available(self):
                return True

            def name(self):
                return "broken"

        with self.assertRaises(TypeError):
            reconcile_traceability_with_provider(self.tmp, graph, BrokenProvider())


if __name__ == "__main__":
    unittest.main()
