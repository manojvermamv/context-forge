#!/usr/bin/env python3
"""Benchmark: Truth Contamination Defense.

Evaluates whether agent speculation, assistant summaries, or tool errors can
accidentally contaminate canonical project truth. Target contamination rate: 0.0%.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.core.authority import EpistemicAuthority


def evaluate_truth_contamination() -> dict[str, float | int]:
    """Test various unauthorized inputs attempting to establish project truth."""
    test_cases = [
        ("decision", "agent_inference", True, False),
        ("decision", "code_observed", True, False),
        ("decision", "external_source", True, False),
        ("decision", "user_explicit", False, False),  # Missing --accept
        ("decision", "user_explicit", True, True),    # Valid!
        ("requirement", "agent_inference", True, False),
        ("requirement", "code_observed", True, False),
        ("requirement", "user_explicit", False, False),
        ("requirement", "user_explicit", True, True), # Valid!
        ("technical", "agent_inference", False, False),# Technical must be code_observed
        ("technical", "code_observed", False, True),
    ]

    total = len(test_cases)
    passed = 0
    contaminations = 0

    for kind, authority, accept, expected_success in test_cases:
        can_accept, _ = EpistemicAuthority.can_accept_intent(kind, authority, accept)
        if can_accept == expected_success:
            passed += 1
        elif can_accept and not expected_success:
            contaminations += 1

    contamination_rate = contaminations / total
    return {
        "total_cases": total,
        "passed": passed,
        "contaminations": contaminations,
        "contamination_rate": contamination_rate,
    }


if __name__ == "__main__":
    res = evaluate_truth_contamination()
    print(f"[eval:truth_contamination] {res['passed']}/{res['total_cases']} passed, rate={res['contamination_rate']*100:.1f}%")
    assert res["contamination_rate"] == 0.0, "Truth contamination detected!"
