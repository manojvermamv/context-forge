#!/usr/bin/env python3
"""Unified runner for Context Forge v2 evaluation benchmarks."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from test_truth_contamination import evaluate_truth_contamination
from test_conflict_resolution import evaluate_conflict_resolution
from test_freshness_detection import evaluate_freshness_detection
from test_context_precision_and_budget import evaluate_precision_and_budget


def run_all_evals() -> int:
    print("=" * 65)
    print(" Context Forge v2 Evaluation Benchmarks")
    print("=" * 65)

    # 1. Truth Contamination Defense
    tc = evaluate_truth_contamination()
    print(f"[EVAL 1] Truth Contamination Defense:")
    print(f"         Cases tested: {tc['total_cases']}")
    print(f"         Contaminations: {tc['contaminations']}")
    print(f"         Contamination Rate: {tc['contamination_rate']*100:.1f}% (target: 0.0%)")
    assert tc["contamination_rate"] == 0.0, "Truth contamination test failed!"
    print("         Result: PASS\n")

    # 2. Cross-Plane Conflict Resolution & Authority Governance
    cr = evaluate_conflict_resolution()
    print(f"[EVAL 2] Cross-Plane Conflict Resolution & Authority Governance:")
    print(f"         Scenarios tested: {cr['total_scenarios']}")
    print(f"         Correct resolutions: {cr['correct']}")
    print(f"         Accuracy: {cr['accuracy']*100:.1f}% (target: 100.0%)")
    assert cr["accuracy"] == 1.0, "Conflict resolution test failed!"
    print("         Result: PASS\n")

    # 3. Living Git Freshness Detection
    fd = evaluate_freshness_detection()
    print(f"[EVAL 3] Living Git Freshness Detection:")
    print(f"         Checks tested: {fd['total']}")
    print(f"         Passed: {fd['passed']}")
    print(f"         Accuracy: {fd['accuracy']*100:.1f}% (target: 100.0%)")
    assert fd["accuracy"] == 1.0, "Freshness detection test failed!"
    print("         Result: PASS\n")

    # 4. Context Precision, Recall, Budget Adherence & Determinism
    pb = evaluate_precision_and_budget()
    print(f"[EVAL 4] Context Precision, Recall, Budget & Determinism:")
    print(f"         Budget adherence: {'PASS' if pb['budget_adherence'] else 'FAIL'} ({pb['rendered_chars']}/{pb['target_budget']} chars)")
    print(f"         Context Recall: {pb['recall']*100:.1f}% (gold items retrieved)")
    print(f"         Context Precision: {pb['precision']*100:.1f}% (relevance vs noise)")
    print(f"         Next-reading prioritized: {'PASS' if pb['next_reading_prioritized'] else 'FAIL'}")
    print(f"         Deterministic compilation: {'PASS' if pb['deterministic'] else 'FAIL'}")
    print(f"         Typed traceability: {'PASS' if pb['has_typed_trace'] else 'FAIL'}")
    print(f"         Provenance completeness: {'PASS' if pb['has_provenance'] else 'FAIL'}")

    assert pb["budget_adherence"], "Budget adherence exceeded configured ceiling!"
    assert pb["recall"] >= 0.75, "Context recall below target threshold!"
    assert pb["precision"] >= 0.75, "Context precision below target threshold!"
    assert pb["deterministic"], "Context compilation is non-deterministic!"
    assert pb["has_typed_trace"], "Traceability graph missing typed edges!"
    assert pb["has_provenance"], "Provenance metadata missing in knowledge records!"
    print("         Result: PASS\n")

    print("=" * 65)
    print(" All current evaluation cases passed.")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(run_all_evals())
