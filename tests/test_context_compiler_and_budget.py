import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.compiler.allocator import allocate_context_budget
from context_forge.compiler.pack import compile_context_pack
from context_forge.cli.commands import cmd_init, cmd_context
from context_forge.core.models import ContextPack


class TestContextCompilerAndBudget(unittest.TestCase):
    def test_strict_rendered_budget_enforcement(self) -> None:
        """Verify that allocator trims sections until rendered text <= max_budget."""
        # Create oversized mock sections
        sections = {
            "authoritative_intent": [
                {"id": f"REQ-{i:03d}", "title": f"Requirement {i}", "authority": "user_explicit", "body": "A" * 500}
                for i in range(10)
            ],
            "current_implementation": [
                {"symbol": f"Class{i}", "path": f"src/class_{i}.py", "details": "B" * 200}
                for i in range(10)
            ],
            "past_experience": [
                {"finding": f"Lesson {i}: " + ("C" * 200), "source": "agentmemory"}
                for i in range(5)
            ],
            "open_questions": [
                {"id": f"Q-{i:03d}", "title": f"Question {i}", "question": "D" * 200}
                for i in range(5)
            ],
            "conflicts_and_staleness": [
                {"type": "DRIFT", "warning": "CRITICAL DRIFT: Code violates REQ-001"}
            ],
            "next_reading": [f"src/file_{i}.py" for i in range(10)],
            "provider_diagnostics": ["Native mapper active"],
        }

        budget = 1200
        trimmed = allocate_context_budget(sections, max_budget=budget, task="Security audit")

        pack = ContextPack(
            task="Security audit",
            budget_chars=budget,
            authoritative_intent=trimmed["authoritative_intent"],
            current_implementation=trimmed["current_implementation"],
            past_experience=trimmed["past_experience"],
            open_questions=trimmed["open_questions"],
            conflicts_and_staleness=trimmed["conflicts_and_staleness"],
            next_reading=trimmed["next_reading"],
            provider_diagnostics=trimmed.get("provider_diagnostics", []),
        )
        rendered = pack.to_text()

        # Critical conflict must survive
        self.assertIn("CRITICAL DRIFT", rendered)
        # Total rendered length must be <= budget (or within minimal header tolerance)
        self.assertLessEqual(len(rendered), budget)

    def test_deterministic_repeatability(self) -> None:
        """Verify identical inputs produce identical rendered ContextPack text."""
        sections = {
            "authoritative_intent": [
                {"id": "REQ-002", "title": "B", "authority": "user_explicit"},
                {"id": "REQ-001", "title": "A", "authority": "user_explicit"},
            ],
            "current_implementation": [
                {"symbol": "Z", "path": "z.py"},
                {"symbol": "A", "path": "a.py"},
            ],
            "conflicts_and_staleness": [
                {"type": "STALE", "warning": "Stale 1"},
                {"type": "DRIFT", "warning": "Drift 1"},
            ],
            "next_reading": ["b.py", "a.py"],
        }

        t1 = allocate_context_budget(sections, max_budget=3000, task="Test")
        t2 = allocate_context_budget(sections, max_budget=3000, task="Test")

        pack1 = ContextPack(task="Test", **t1).to_text()
        pack2 = ContextPack(task="Test", **t2).to_text()
        self.assertEqual(pack1, pack2)

    def test_task_specific_next_reading_prioritized(self) -> None:
        """Verify affected source paths and matched records come before generic docs."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cmd_init(repo)

            # Create source files
            (repo / "auth.py").write_text("def auth(): pass\n", encoding="utf-8")
            (repo / "tokens.py").write_text("def tokens(): pass\n", encoding="utf-8")

            pack = compile_context_pack(repo, "auth tokens", paths=["auth.py", "tokens.py"])
            reading = pack.next_reading

            self.assertIn("auth.py", reading)
            self.assertIn("tokens.py", reading)
            # auth.py must be earlier in the reading list than .brain/current-state.md
            auth_idx = reading.index("auth.py")
            if ".brain/current-state.md" in reading:
                cs_idx = reading.index(".brain/current-state.md")
                self.assertLess(auth_idx, cs_idx)

    def test_cmd_context_default_renders_full_pack_and_pointers_only(self) -> None:
        """Verify cmd_context outputs full Markdown pack by default and handles flags."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cmd_init(repo)

            # 1. Default output contains markdown headings
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cmd_context(repo, "initialization task")
            out = buf.getvalue()
            self.assertIn("# Context Pack — Task:", out)

            # 2. Pointers-only flag
            buf_ptrs = io.StringIO()
            with patch("sys.stdout", buf_ptrs):
                cmd_context(repo, "initialization task", pointers_only=True)
            out_ptrs = buf_ptrs.getvalue()
            self.assertIn("Read these files, in order:", out_ptrs)

            # 3. JSON format flag
            buf_json = io.StringIO()
            with patch("sys.stdout", buf_json):
                cmd_context(repo, "initialization task", output_format="json")
            out_json = json.loads(buf_json.getvalue())
            self.assertEqual(out_json["task"], "initialization task")
            self.assertIn("sections", out_json)


if __name__ == "__main__":
    unittest.main()
