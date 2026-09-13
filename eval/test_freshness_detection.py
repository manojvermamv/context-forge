#!/usr/bin/env python3
"""Benchmark: Living Git Freshness Detection.

Evaluates living Git-anchored freshness across working tree and commit history:
1. Clean working tree, no changes -> fresh.
2. Uncommitted dirty working tree -> possibly_stale.
3. Missing file on disk -> stale.
4. Record at commit A, file changed at commit B, working tree clean -> possibly_stale (Commit-anchored).
5. Record at commit A, unrelated file changed at commit B, working tree clean -> fresh.
6. Rename after observed commit -> stale.
7. File modified then reverted exactly (tree hash identical between observed commit and HEAD) -> fresh.

Target accuracy: 100.0%.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.traceability.freshness import check_record_freshness


def run_git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return res.stdout.strip()


def evaluate_freshness_detection() -> dict[str, float | int]:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)

        # Initialize real git repository for living freshness tests
        run_git(repo, "init")
        run_git(repo, "config", "user.name", "ContextForgeTester")
        run_git(repo, "config", "user.email", "test@contextforge.local")

        src = repo / "src"
        src.mkdir()
        f_auth = src / "auth.py"
        f_auth.write_text("def login(): pass\n", encoding="utf-8")
        f_db = src / "db.py"
        f_db.write_text("def connect(): pass\n", encoding="utf-8")
        f_other = src / "other.py"
        f_other.write_text("def ping(): pass\n", encoding="utf-8")

        run_git(repo, "add", ".")
        run_git(repo, "commit", "-m", "Commit A: initial code")
        sha_a = run_git(repo, "rev-parse", "HEAD")

        # Create records pointing to commit A
        rec_auth = repo / "rec_auth.md"
        rec_auth.write_text(f"---\ncommit_sha: {sha_a}\n---\n## Related paths\n- `src/auth.py`\n", encoding="utf-8")

        rec_other = repo / "rec_other.md"
        rec_other.write_text(f"---\ncommit_sha: {sha_a}\n---\n## Related paths\n- `src/other.py`\n", encoding="utf-8")

        rec_db = repo / "rec_db.md"
        rec_db.write_text(f"---\ncommit_sha: {sha_a}\n---\n## Related paths\n- `src/db.py`\n", encoding="utf-8")

        # 1. No changes at all -> fresh
        t1 = check_record_freshness(repo, rec_auth) == "fresh"

        # 2. Dirty working tree on auth.py -> possibly_stale
        f_auth.write_text("def login(): # dirty change\n    pass\n", encoding="utf-8")
        t2 = check_record_freshness(repo, rec_auth) == "possibly_stale"
        # rec_other is clean and unrelated -> fresh
        t3 = check_record_freshness(repo, rec_other) == "fresh"

        # Commit dirty change to commit B -> working tree is now clean!
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-m", "Commit B: updated auth")
        # 4. Commit-anchored: working tree is clean, but auth.py changed since sha_a!
        t4 = check_record_freshness(repo, rec_auth) == "possibly_stale"
        # 5. Commit-anchored: other.py did NOT change since sha_a!
        t5 = check_record_freshness(repo, rec_other) == "fresh"

        # 6. File deleted on disk -> stale
        f_db.unlink()
        t6 = check_record_freshness(repo, rec_db) == "stale"
        # Restore db.py for rename test
        f_db.write_text("def connect(): pass\n", encoding="utf-8")

        # 7. File renamed in commit C
        run_git(repo, "mv", "src/db.py", "src/database.py")
        run_git(repo, "commit", "-m", "Commit C: renamed db.py to database.py")
        # rec_db points to src/db.py which was renamed in git history
        t7 = check_record_freshness(repo, rec_db) == "stale"

        # 8. Exact revert semantics: modify other.py in commit D, then revert exactly in commit E
        f_other.write_text("def ping(): pass # temp mod\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-m", "Commit D: temp modification")
        f_other.write_text("def ping(): pass\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-m", "Commit E: exact revert back to sha_a content")
        # Since tree diff between sha_a and HEAD for other.py is zero, it should be fresh!
        t8 = check_record_freshness(repo, rec_other) == "fresh"

        tests = [t1, t2, t3, t4, t5, t6, t7, t8]
        passed = sum(1 for t in tests if t)
        total = len(tests)

        return {
            "total": total,
            "passed": passed,
            "accuracy": passed / total,
        }


if __name__ == "__main__":
    res = evaluate_freshness_detection()
    print(f"[eval:freshness_detection] {res['passed']}/{res['total']} passed, accuracy={res['accuracy']*100:.1f}%")
    assert res["accuracy"] == 1.0, f"Freshness detection failure: {res}"

