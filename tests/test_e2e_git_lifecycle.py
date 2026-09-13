from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import (
    cmd_init,
    cmd_scan,
    cmd_map,
    cmd_index,
    cmd_context,
    cmd_search,
    cmd_review,
)
from context_forge.cli.doctor import cmd_doctor, cmd_status, cmd_lint
from context_forge.knowledge.update import create_knowledge_record
from context_forge.knowledge.consolidate import plan_or_apply_consolidation
from context_forge.traceability.sync import reconcile_sync
from context_forge.traceability.freshness import (
    check_record_freshness,
    evaluate_record_freshness,
    update_repository_freshness,
)
from context_forge.store.paths import brain_paths, read_text


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return res.stdout.strip()


class E2EGitLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        git(self.tmp, "init")
        git(self.tmp, "config", "user.name", "ContextForgeTester")
        git(self.tmp, "config", "user.email", "tester@contextforge.local")

        # Initial source files
        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        (self.tmp / "tests").mkdir(parents=True, exist_ok=True)
        (self.tmp / "src" / "engine.py").write_text("class Engine: pass\n", encoding="utf-8")
        (self.tmp / "tests" / "test_engine.py").write_text("def test_engine(): pass\n", encoding="utf-8")

        git(self.tmp, "add", ".")
        self.c1 = git(self.tmp, "commit", "-m", "Initial commit")
        self.c1_sha = git(self.tmp, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_complete_git_anchored_lifecycle(self):
        """Execute full end-to-end Context Forge v2 lifecycle with commit anchoring, freshness, and governance."""
        p = brain_paths(self.tmp)

        # 1. init
        cmd_init(self.tmp)
        self.assertTrue(p["root"].exists())
        self.assertTrue(p["policies"].exists())

        # 2. scan
        cmd_scan(self.tmp)
        tech_map = p["technical"] / "codebase-map.md"
        self.assertTrue(tech_map.exists())
        text_map = read_text(tech_map)
        self.assertIn(f"commit_sha: {self.c1_sha}", text_map)

        # 3. map
        cmd_map(self.tmp)
        self.assertTrue(p["map"].exists())

        # 4. index
        cmd_index(self.tmp)
        self.assertTrue(p["search_index_db"].exists() or p["search_index_json"].exists())

        # 5. update (REQ, ADR, and POL)
        rc_req = create_knowledge_record(
            repo=self.tmp,
            kind="requirement",
            title="Deterministic Replay Execution",
            body="All engine executions must replay deterministically.",
            authority="user_explicit",
            evidence="PRD Chapter 2",
            scope=["src/engine.py", "tests/test_engine.py"],
            accept=True,
        )
        self.assertEqual(rc_req, 0)

        rc_adr = create_knowledge_record(
            repo=self.tmp,
            kind="decision",
            title="Single-Threaded Replay Loop",
            body="Replay loop must avoid wall-clock sleeps to ensure determinism.",
            authority="user_explicit",
            evidence="Architecture ADR-014",
            scope=["src/engine.py"],
            accept=True,
        )
        self.assertEqual(rc_adr, 0)

        rc_pol = create_knowledge_record(
            repo=self.tmp,
            kind="policy",
            title="No Unpersisted Wall-Clock State",
            body="Non-persisted wall-clock timestamps are strictly forbidden in execution paths.",
            authority="policy_mandate",
            evidence="Regulatory audit requirement",
            scope=["src/engine.py"],
            accept=True,
        )
        self.assertEqual(rc_pol, 0)

        # Commit knowledge base
        git(self.tmp, "add", ".brain")
        git(self.tmp, "commit", "-m", "Commit canonical memory")
        c2_sha = git(self.tmp, "rev-parse", "HEAD")

        # 6. context compilation
        pack = cmd_context(self.tmp, "Deterministic replay validation", paths=["src/engine.py"], pointers_only=False)
        self.assertIsNotNone(pack)
        rendered = pack.to_text()
        self.assertIn("Deterministic Replay Execution", rendered)
        self.assertIn("Single-Threaded Replay Loop", rendered)
        self.assertIn("src/engine.py", pack.next_reading)

        # 7. sync: add an external file, commit it
        (self.tmp / "src" / "router.py").write_text("class Router: pass\n", encoding="utf-8")
        git(self.tmp, "add", "src/router.py")
        git(self.tmp, "commit", "-m", "Add router")
        c3_sha = git(self.tmp, "rev-parse", "HEAD")

        rc_sync = reconcile_sync(self.tmp, explicit_paths=["src/router.py"], apply=True)
        self.assertEqual(rc_sync, 0)

        # 8. review
        rc_rev = cmd_review(self.tmp, if_due=False, pending=True)
        self.assertEqual(rc_rev, 0)

        # 9. search
        buf_search = io.StringIO()
        with redirect_stdout(buf_search):
            cmd_search(self.tmp, "Deterministic")
        self.assertIn("Deterministic", buf_search.getvalue())

        # 10. status & doctor & lint
        buf_doc = io.StringIO()
        with redirect_stdout(buf_doc):
            cmd_doctor(self.tmp)
        doc_out = buf_doc.getvalue()
        self.assertIn("native context forge:", doc_out)
        self.assertIn("operational", doc_out)
        self.assertIn("codebase memory mcp (cbm)", doc_out)
        self.assertIn("agentmemory", doc_out)

        buf_stat = io.StringIO()
        with redirect_stdout(buf_stat):
            cmd_status(self.tmp)
        self.assertIn("initialized", buf_stat.getvalue())

        lint_res = cmd_lint(self.tmp)
        self.assertIsInstance(lint_res, list)

        # 11. consolidate
        rc_cons = plan_or_apply_consolidation(self.tmp, apply=False)
        self.assertEqual(rc_cons, 0)

        # 12. Commit-anchored Freshness & Exact-Revert
        req_file = list(p["requirements"].glob("REQ-*.md"))[0]
        # Initially fresh
        st_initial = evaluate_record_freshness(self.tmp, req_file)
        self.assertIn(st_initial.status, ("fresh", "unverified"))

        # Modify linked file and commit
        orig_content = (self.tmp / "src" / "engine.py").read_text(encoding="utf-8")
        (self.tmp / "src" / "engine.py").write_text("class Engine: # modified\n    pass\n", encoding="utf-8")
        git(self.tmp, "add", "src/engine.py")
        git(self.tmp, "commit", "-m", "Modify engine")
        c4_sha = git(self.tmp, "rev-parse", "HEAD")

        st_modified = evaluate_record_freshness(self.tmp, req_file)
        self.assertEqual(st_modified.status, "possibly_stale")

        # Now revert engine.py back exactly to original content and commit
        (self.tmp / "src" / "engine.py").write_text(orig_content, encoding="utf-8")
        git(self.tmp, "add", "src/engine.py")
        git(self.tmp, "commit", "-m", "Exact revert of engine")
        c5_sha = git(self.tmp, "rev-parse", "HEAD")

        st_reverted = evaluate_record_freshness(self.tmp, req_file)
        self.assertEqual(st_reverted.status, "fresh")
        self.assertIn("Exact revert", st_reverted.reason)


if __name__ == "__main__":
    unittest.main()
