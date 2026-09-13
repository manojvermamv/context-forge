#!/usr/bin/env python3
"""Benchmark: Cross-Plane Conflict Resolution.

Evaluates whether authoritative ADRs reliably override contradictory agent memories.
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


def evaluate_conflict_resolution() -> dict[str, float | int]:
    scenarios = [
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

    correct = 0
    for adr, exp, expected_winner in scenarios:
        conflicts = ConflictResolver.detect_conflicts([adr], [exp])
        if conflicts and conflicts[0].get("winner") == expected_winner:
            correct += 1

    accuracy = correct / len(scenarios)
    return {
        "total_scenarios": len(scenarios),
        "correct": correct,
        "accuracy": accuracy,
    }


if __name__ == "__main__":
    res = evaluate_conflict_resolution()
    print(f"[eval:conflict_resolution] {res['correct']}/{res['total_scenarios']} resolved correctly, accuracy={res['accuracy']*100:.1f}%")
    assert res["accuracy"] == 1.0, "Conflict resolution failure!"
