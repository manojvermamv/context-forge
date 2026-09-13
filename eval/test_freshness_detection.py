#!/usr/bin/env python3
"""Benchmark: Freshness Detection.

Evaluates whether code modifications correctly invalidate affected records.
Target accuracy: 100.0%.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.traceability.freshness import check_record_freshness


def evaluate_freshness_detection() -> dict[str, float | int]:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        src = repo / "src"
        src.mkdir()
        f1 = src / "auth.py"
        f1.write_text("def login(): pass\n", encoding="utf-8")
        f2 = src / "db.py"
        f2.write_text("def connect(): pass\n", encoding="utf-8")

        rec1 = repo / "rec1.md"
        rec1.write_text("## Related paths\n- `src/auth.py`\n", encoding="utf-8")
        rec2 = repo / "rec2.md"
        rec2.write_text("## Related paths\n- `src/db.py`\n", encoding="utf-8")

        # Scenario 1: No changes -> fresh
        s1 = check_record_freshness(repo, rec1, set()) == "fresh"

        # Scenario 2: auth.py changed -> rec1 is possibly_stale, rec2 is fresh
        changed = {"src/auth.py"}
        s2 = check_record_freshness(repo, rec1, changed) == "possibly_stale"
        s3 = check_record_freshness(repo, rec2, changed) == "fresh"

        # Scenario 3: db.py deleted -> rec2 is stale
        f2.unlink()
        s4 = check_record_freshness(repo, rec2, set()) == "stale"

        passed = sum([s1, s2, s3, s4])
        total = 4
        return {
            "total": total,
            "passed": passed,
            "accuracy": passed / total,
        }


if __name__ == "__main__":
    res = evaluate_freshness_detection()
    print(f"[eval:freshness_detection] {res['passed']}/{res['total']} passed, accuracy={res['accuracy']*100:.1f}%")
    assert res["accuracy"] == 1.0, "Freshness detection failure!"
