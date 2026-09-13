#!/usr/bin/env python3
"""Benchmark: Cross-Plane Conflict Resolution & Drift Governance.

Evaluates domain-aware epistemic conflict resolution:
1. Experience vs Intent: Intent strictly subordinates agent advice (ADVICE_REJECTED).
2. Intent vs Implementation: Divergence produces DRIFT / VIOLATION alerts without deleting reality.
3. Live Code vs Recorded Technical Doc: Code truth supersedes stale records (RECORD_STALE).
4. Newer ADR vs Older ADR: Supersession resolution (SUPERSEDED).
5. User Instruction vs Historical Decision: Direct user override (USER_OVERRIDE).

Target accuracy: 100.0%.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.compiler.conflict import ConflictResolver
from context_forge.core.authority import AuthorityDomain, EpistemicAuthority, ResolutionDisposition


def evaluate_conflict_resolution() -> dict[str, float | int]:
    # 1. Experience vs Intent scenarios (Intent wins, advice rejected)
    exp_vs_intent_scenarios = [
        (
            {"id": "ADR-001", "title": "Kafka rejected", "body": "Kafka is rejected; use RabbitMQ."},
            {"finding": "Use kafka for high-throughput message streaming", "source": "agentmemory"},
            "ADR-001",
        ),
        (
            {"id": "ADR-002", "title": "Mongo rejected", "body": "MongoDB is forbidden; use PostgreSQL."},
            {"finding": "Use mongo for flexible document storage", "source": "agentmemory"},
            "ADR-002",
        ),
        (
            {"id": "ADR-003", "title": "Avoid raw threads", "body": "Raw threading is avoided; use asyncio."},
            {"finding": "Use threads for concurrency in this module", "source": "agentmemory"},
            "ADR-003",
        ),
    ]

    # 2. Intent vs Implementation Drift scenarios (produces DRIFT / VIOLATION)
    drift_scenarios = [
        (
            {"id": "ADR-010", "title": "Every order must pass RiskGate", "body": "Mandatory check: all orders must pass RiskGate."},
            {"details": "Fast-path order execution bypasses RiskGate directly", "symbol": "fast_order_exec", "path": "src/order.py"},
            "DRIFT",
        ),
        (
            {"id": "REQ-020", "title": "Direct DB access is forbidden", "body": "Handlers must not call DB directly; repository required."},
            {"details": "PaymentHandler calls db directly without repository", "symbol": "PaymentHandler.pay", "path": "src/pay.py"},
            "DRIFT",
        ),
    ]

    # 3. Live Code vs Stale Technical Record (RECORD_STALE)
    stale_scenarios = [
        (
            {"id": "TECH-001", "title": "Synchronous order worker", "body": "Worker processes orders synchronously.", "scope": ["src/worker.py"]},
            {"details": "sync_worker removed; orders use async pipeline", "path": "src/worker.py"},
            "RECORD_STALE",
        )
    ]

    # 4. Pairwise General Cross-Plane Interactions
    pairwise_scenarios = [
        (
            {"id": "ADR-005", "kind": "decision", "authority": "user_explicit", "authority_level": 90, "title": "Use RabbitMQ"},
            {"id": "MEM-099", "kind": "memory", "authority": "episodic_memory", "authority_level": 40, "title": "Try Kafka"},
            ResolutionDisposition.ADVICE_REJECTED.value,
        ),
        (
            {"id": "ADR-010", "kind": "decision", "authority": "user_explicit", "authority_level": 90, "title": "RiskGate required"},
            {"id": "SYM-001", "kind": "technical", "authority": "code_observed", "authority_level": 80, "details": "bypasses RiskGate"},
            ResolutionDisposition.DRIFT.value,
        ),
        (
            {"id": "USER-NOW", "kind": "decision", "authority": "user_explicit", "authority_level": 100, "title": "Adopt SQLite today"},
            {"id": "ADR-001", "kind": "decision", "authority": "user_explicit", "authority_level": 90, "title": "PostgreSQL required"},
            ResolutionDisposition.USER_OVERRIDE.value,
        ),
    ]

    total_scenarios = len(exp_vs_intent_scenarios) + len(drift_scenarios) + len(stale_scenarios) + len(pairwise_scenarios)
    correct = 0

    # Test 1: Exp vs Intent
    for adr, exp, expected_winner in exp_vs_intent_scenarios:
        conflicts = ConflictResolver.detect_conflicts([adr], [exp])
        if conflicts and conflicts[0].get("winner") == expected_winner and conflicts[0].get("disposition") == ResolutionDisposition.ADVICE_REJECTED.value:
            correct += 1

    # Test 2: Intent vs Code Drift
    for adr, fact, expected_disp in drift_scenarios:
        drifts = ConflictResolver.detect_code_drift([adr], [fact])
        if drifts and drifts[0].get("disposition") == expected_disp:
            correct += 1

    # Test 3: Stale Records
    for tech, fact, expected_disp in stale_scenarios:
        stales = ConflictResolver.detect_stale_records([tech], [fact])
        if stales and stales[0].get("disposition") == expected_disp:
            correct += 1

    # Test 4: Pairwise
    for a, b, expected_disp in pairwise_scenarios:
        res = ConflictResolver.resolve_cross_plane(a, b)
        if res.get("disposition") == expected_disp:
            correct += 1

    accuracy = correct / total_scenarios
    return {
        "total_scenarios": total_scenarios,
        "correct": correct,
        "accuracy": accuracy,
    }


if __name__ == "__main__":
    res = evaluate_conflict_resolution()
    print(f"[eval:conflict_resolution] {res['correct']}/{res['total_scenarios']} resolved correctly, accuracy={res['accuracy']*100:.1f}%")
    assert res["accuracy"] == 1.0, f"Conflict resolution failure: {res}"

