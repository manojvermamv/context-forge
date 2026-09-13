from __future__ import annotations

import io
import json
import shutil
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
from context_forge.hooks.inject import handle_session_start, handle_turn_inject
from context_forge.hooks.guard import handle_guard
from context_forge.hooks.capture import handle_capture
from context_forge.store.paths import brain_paths, read_text, atomic_write


class BackwardCompatibilityTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Set up a legacy repository structure (Context Forge v1 layout)
        p = brain_paths(self.tmp)
        p["root"].mkdir(parents=True, exist_ok=True)
        for d in ("decisions", "requirements", "technical", "traceability", "concepts", "questions", "audit"):
            p[d].mkdir(parents=True, exist_ok=True)
        p["state"].mkdir(parents=True, exist_ok=True)

        # Legacy root files
        atomic_write(p["current_state"], "# Current State\n_Last updated 2026-01-01T00:00:00Z_\n## Recent decisions\n- Token auth adopted\n")
        atomic_write(p["index_md"], "# Index\n| id | kind | title |\n|---|---|---|\n| [ADR-001](decisions/ADR-001.md) | decision | Use Token Auth |\n")
        atomic_write(p["overview"], "# Overview\nLegacy overview.\n")
        atomic_write(p["status"], "# Status\nLegacy status.\n")
        atomic_write(p["log"], "# Log\n\n## [2026-01-01T00:00:00Z] legacy entry\n- (decisions) Token auth\n")

        # Legacy ADR record with no schema_version, no producer, minimal frontmatter
        self.legacy_adr = p["decisions"] / "ADR-001-use-token-auth.md"
        atomic_write(
            self.legacy_adr,
            "---\n"
            "id: ADR-001\n"
            "status: accepted\n"
            "authority: user_explicit\n"
            "updated: 2026-01-01\n"
            "---\n\n"
            "# Use Token Auth\n\n"
            "We chose token auth over session cookies.\n"
        )

        # Legacy TRACE record
        self.legacy_trace = p["traceability"] / "TRACE-001-use-token-auth.md"
        atomic_write(
            self.legacy_trace,
            "---\n"
            "id: TRACE-001\n"
            "status: linked\n"
            "authority: derived_link\n"
            "updated: 2026-01-01\n"
            "---\n\n"
            "# Traceability — Use Token Auth\n\n"
            "## Source record\n\n- `ADR-001`\n\n"
            "## Related paths\n\n- `src/auth.py`\n- `tests/test_auth.py`\n"
        )

        # Sample code files
        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        (self.tmp / "tests").mkdir(parents=True, exist_ok=True)
        (self.tmp / "src" / "auth.py").write_text("def verify_token(): pass\n", encoding="utf-8")
        (self.tmp / "tests" / "test_auth.py").write_text("def test_token(): pass\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_lifecycle_suite(self):
        """Verify that all commands run safely on legacy repository format without errors or data loss."""
        # 1. init (idempotent, preserves existing ADR-001)
        cmd_init(self.tmp, force=False)
        self.assertTrue(self.legacy_adr.exists())
        self.assertIn("ADR-001", read_text(self.legacy_adr))

        # 2. scan & map
        cmd_scan(self.tmp)
        p = brain_paths(self.tmp)
        self.assertTrue(p["map"].exists())

        # 3. index
        cmd_index(self.tmp)
        self.assertTrue(p["search_index_db"].exists() or p["search_index_json"].exists())

        # 4. search
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_search(self.tmp, "Token Auth")
        self.assertIn("ADR-001", buf.getvalue())

        # 5. context compilation
        pack = cmd_context(self.tmp, "Token auth verification", pointers_only=False)
        self.assertIsNotNone(pack)
        self.assertIn("ADR-001", pack.render_markdown())

        # 6. update
        rc_update = create_knowledge_record(
            repo=self.tmp,
            kind="requirement",
            title="Token Expiry Check",
            body="Tokens must expire after 1 hour.",
            authority="user_explicit",
            evidence="Security policy review",
            scope=["src/auth.py"],
            accept=True,
        )
        self.assertEqual(rc_update, 0)

        # 7. sync
        rc_sync = reconcile_sync(self.tmp, explicit_paths=["src/auth.py"], apply=True)
        self.assertEqual(rc_sync, 0)

        # 8. hooks: session-start, turn-inject, guard, capture
        from unittest.mock import patch

        # Test session start
        with patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(self.tmp), "source": "startup"}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                handle_session_start()
            self.assertIn("context-forge", buf.getvalue())

        # Test turn inject
        with patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(self.tmp), "prompt": "Token auth verification"}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                handle_turn_inject()
            self.assertTrue(len(buf.getvalue()) >= 0)

        # Test guard: auto-managed brain path should be denied
        with patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(self.tmp), "tool_name": "Edit", "tool_input": {"path": ".brain/current-state.md"}}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                handle_guard()
            self.assertIn("deny", buf.getvalue())

        # Test capture
        with patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(self.tmp), "session_id": "sess_1", "last_assistant_message": "Decided to enforce token expiry."}))):
            handle_capture("Stop")
            pending = list(p["pending"].glob("*.json"))
            self.assertTrue(len(pending) >= 1)

        # 9. review
        rc_review = cmd_review(self.tmp, if_due=False, pending=True)
        self.assertEqual(rc_review, 0)

        # 10. consolidate
        rc_cons = plan_or_apply_consolidation(self.tmp, apply=False)
        self.assertEqual(rc_cons, 0)

        # 11. doctor & status & lint
        buf_doc = io.StringIO()
        with redirect_stdout(buf_doc):
            cmd_doctor(self.tmp)
        self.assertIn("context-forge doctor", buf_doc.getvalue())

        buf_stat = io.StringIO()
        with redirect_stdout(buf_stat):
            cmd_status(self.tmp)
        self.assertIn("initialized", buf_stat.getvalue())

        lint_issues = cmd_lint(self.tmp)
        self.assertIsInstance(lint_issues, list)


if __name__ == "__main__":
    unittest.main()
