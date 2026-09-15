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

        impl_edges = [e for e in edges if e["edge_type"] in (TraceEdgeType.IMPLEMENTS.value, TraceEdgeType.IMPLEMENTS_REQUIREMENT.value)]
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

    def test_symbol_existence_does_not_verify_requested_relationship(self):
        """Release blocker: CBM proving symbol existence must NOT verify a requested relationship edge."""
        create_knowledge_record(
            repo=self.tmp,
            kind="requirement",
            title="Token Service Requirement",
            body="TokenService must implement authentication.",
            authority="user_explicit",
            evidence="Spec",
            scope=["src/router.py"],
            accept=True,
        )
        graph = resolve_traceability_graph(self.tmp)
        # Give edge a specific symbol and relationship
        graph[0]["edges"][0]["symbol"] = "TokenService"
        graph[0]["edges"][0]["relationship"] = "implements"

        class MockSymbolOnlyProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.OK,
                    data={
                        "symbol": "TokenService",
                        "path": path,
                        "reference_verified": True,
                        "symbol_verified": True,
                        "relationship_requested": True,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "verification_kind": "symbol_graph",
                        "confidence": 0.90,
                        "edge_confidence": 0.50,
                    },
                    provider="cbm",
                    diagnostic="Symbol 'TokenService' found, but relationship 'implements' not found in AST.",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockSymbolOnlyProvider())
        edge = reconciled[0]["edges"][0]
        self.assertNotEqual(edge["verification_state"], "verified")
        self.assertEqual(edge["verification_state"], "partially_verified")
        self.assertTrue(edge["symbol_verified"])
        self.assertFalse(edge["relationship_verified"])
        self.assertFalse(edge["structurally_verified"])
        self.assertLessEqual(edge["confidence"], 0.50)
        self.assertNotEqual(reconciled[0]["verification_state"], "verified")

    def test_filesystem_only_evidence_does_not_verify_relationship(self):
        """Filesystem existence alone must not verify a relationship edge."""
        create_knowledge_record(
            repo=self.tmp,
            kind="requirement",
            title="File Requirement",
            body="Requirement on router file.",
            authority="user_explicit",
            evidence="Spec",
            scope=["src/router.py"],
            accept=True,
        )
        graph = resolve_traceability_graph(self.tmp)
        graph[0]["edges"][0]["relationship"] = "implements"

        class MockFilesystemProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.OK,
                    data={
                        "path": path,
                        "reference_verified": True,
                        "symbol_verified": False,
                        "relationship_requested": True,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "verification_kind": "filesystem",
                        "confidence": 0.50,
                        "edge_confidence": 0.0,
                    },
                    provider="cbm",
                    diagnostic="Path exists on filesystem; AST relation unverified.",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockFilesystemProvider())
        edge = reconciled[0]["edges"][0]
        self.assertEqual(edge["verification_state"], "unverified")
        self.assertTrue(edge["reference_verified"])
        self.assertFalse(edge["relationship_verified"])
        self.assertEqual(edge["confidence"], 0.50)

    def test_cbm_cannot_upgrade_project_governance_edges(self):
        """External code provider must not be allowed to verify PROJECT_GOVERNANCE edges (e.g. SATISFIES, DERIVED_FROM)."""
        graph = [
            {
                "canonical_id": "REQ-001",
                "verification_state": "unverified",
                "edges": [
                    {
                        "edge_type": TraceEdgeType.SATISFIES.value,
                        "verification_domain": "PROJECT_GOVERNANCE",
                        "target_ref": "ADR-002",
                        "target_kind": "decision",
                        "verification_state": "unverified",
                        "confidence": 0.5,
                        "evidence": "Canonical governance link",
                    }
                ],
            }
        ]

        class MockOmniscientProvider:
            def is_available(self):
                return True

            def name(self):
                return "cbm"

            def verify_reference(self, repo_path, path, symbol=None, relationship=None):
                return ProviderResult(
                    status=ProviderStatus.OK,
                    data={"structurally_verified": True, "relationship_verified": True, "confidence": 1.0},
                    provider="cbm",
                )

        reconciled = reconcile_traceability_with_provider(self.tmp, graph, MockOmniscientProvider())
        edge = reconciled[0]["edges"][0]
        # Must NOT be modified or upgraded to verified by CBM!
        self.assertEqual(edge["verification_state"], "unverified")
        self.assertEqual(edge["confidence"], 0.5)
        self.assertNotIn("cbm", edge.get("provider", ""))

    def test_aggregate_state_partially_verified(self):
        """When edges contain partially_verified (without verified), aggregate state must be partially_verified."""
        from context_forge.traceability.resolver import _recompute_graph_aggregate_state
        graph = [
            {
                "canonical_id": "REQ-PARTIAL",
                "verification_state": "unverified",
                "edges": [
                    {
                        "edge_type": TraceEdgeType.IMPLEMENTS_REQUIREMENT.value,
                        "verification_state": "partially_verified",
                    },
                    {
                        "edge_type": TraceEdgeType.VERIFIES_WITH.value,
                        "verification_state": "unverified",
                    },
                ],
            }
        ]
        _recompute_graph_aggregate_state(graph)
        self.assertEqual(graph[0]["verification_state"], "partially_verified")

    def test_trace_edge_domain_separation(self):
        """Governance-to-code edges map to PROJECT_GOVERNANCE; code structural edges map to CODE_STRUCTURE."""
        from context_forge.traceability.resolver import edge_type_to_domain
        self.assertEqual(edge_type_to_domain(TraceEdgeType.IMPLEMENTS_REQUIREMENT).value, "PROJECT_GOVERNANCE")
        self.assertEqual(edge_type_to_domain(TraceEdgeType.TRACES_TO_CODE).value, "PROJECT_GOVERNANCE")
        self.assertEqual(edge_type_to_domain(TraceEdgeType.SATISFIES).value, "PROJECT_GOVERNANCE")
        self.assertEqual(edge_type_to_domain(TraceEdgeType.IMPLEMENTS_INTERFACE).value, "CODE_STRUCTURE")
        self.assertEqual(edge_type_to_domain(TraceEdgeType.INHERITS).value, "CODE_STRUCTURE")
        self.assertEqual(edge_type_to_domain(TraceEdgeType.CALLS).value, "CODE_STRUCTURE")


if __name__ == "__main__":
    unittest.main()
