#!/usr/bin/env python3
"""Native, dependency-free regression tests for context-forge.

Run with:

    python tests/smoke_test.py

The suite deliberately uses temporary repositories and the current Python
interpreter.  It is therefore usable on Windows without WSL/Git Bash, while
also running unchanged on Unix hosts.

The tests specify the safety contract of the capture/review pipeline:

* lifecycle hooks may create a pending candidate, but cannot mutate canonical
  project memory;
* a named candidate requires one explicit ``review --approve ID`` promotion;
* duplicate lifecycle notifications cannot create duplicate candidates or
  duplicate promoted facts; and
* untrusted hook input, including secrets and Codex patch payload variants,
  cannot bypass the memory guard.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "scripts" / "brain.py"
INSTALLER = ROOT / "install.py"


class ContextForgeSmokeTests(unittest.TestCase):
    """Exercise the public CLI and hook JSON contract in isolated repos."""

    maxDiff = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="context-forge-smoke-")
        self.repo = Path(self._tmp.name) / "sample-repo"
        (self.repo / ".git").mkdir(parents=True)
        source = self.repo / "src" / "auth.py"
        source.parent.mkdir()
        source.write_text(
            "class AuthManager:\n"
            "    def login(self, username, password):\n"
            "        return True\n",
            encoding="utf-8",
        )
        self.run_brain("init", str(self.repo))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_brain(
        self,
        *args: str,
        hook_payload: object | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the engine through its public CLI with optional hook JSON stdin."""
        runtime_env = os.environ.copy()
        if env:
            runtime_env.update(env)
        stdin = None if hook_payload is None else json.dumps(hook_payload)
        result = subprocess.run(
            [sys.executable, str(BRAIN), *args],
            input=stdin,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=runtime_env,
            cwd=str(self.repo),
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                "brain command failed\n"
                f"command: {args!r}\n"
                f"exit: {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return result

    @property
    def brain_dir(self) -> Path:
        return self.repo / ".brain"

    def canonical_snapshot(self) -> dict[str, bytes]:
        """Return the canonical pages whose contents hooks must not mutate.

        Pending-review artifacts and the regenerable ``.state`` directory are
        intentionally excluded: both are staging/runtime state, not durable
        memory injected into a later session.
        """
        return {
            str(page.relative_to(self.brain_dir)).replace("\\", "/"): page.read_bytes()
            for page in self.brain_dir.rglob("*.md")
            if ".state" not in page.parts
        }

    def capture(self, event: str, message: str, session_id: str = "smoke-session") -> None:
        self.run_brain(
            "capture",
            "--event",
            event,
            hook_payload={
                "hook_event_name": event.title().replace("precompact", "PreCompact"),
                "cwd": str(self.repo),
                "session_id": session_id,
                "last_assistant_message": message,
            },
        )

    def pending_ids(self) -> list[str]:
        """Read candidate IDs from the public ``review --pending`` interface.

        The engine may render the list as JSON or readable text.  In readable
        text each candidate must expose an explicit ``ID:``/``ID=`` label so a
        person can copy it into ``review --approve ID`` without guessing.
        """
        result = self.run_brain("review", str(self.repo), "--pending")
        output = result.stdout.strip()
        if not output:
            return []

        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            candidates = decoded.get("candidates", decoded.get("pending", []))
            if isinstance(candidates, list):
                ids = [item.get("id") for item in candidates if isinstance(item, dict)]
                return [candidate_id for candidate_id in ids if isinstance(candidate_id, str)]
        if isinstance(decoded, list):
            ids = [item.get("id") for item in decoded if isinstance(item, dict)]
            return [candidate_id for candidate_id in ids if isinstance(candidate_id, str)]

        ids = re.findall(
            r"(?im)\b(?:candidate\s+)?id\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9._:-]{3,})`?",
            output,
        )
        if ids:
            return ids
        self.fail(
            "`review --pending` returned nonempty output without a usable candidate ID.\n"
            f"stdout:\n{result.stdout}\n"
            "Expose each candidate as `ID: <value>` or JSON with an `id` field."
        )

    def hook_guard(self, tool_name: str, tool_input: object) -> str:
        result = self.run_brain(
            "guard",
            hook_payload={
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "cwd": str(self.repo),
            },
        )
        return result.stdout

    def assert_guard_denies(self, tool_name: str, tool_input: object) -> None:
        output = self.hook_guard(tool_name, tool_input)
        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            self.fail(f"guard did not emit valid denial JSON: {output!r} ({exc})")
        hook_output = response.get("hookSpecificOutput", {})
        self.assertEqual("deny", hook_output.get("permissionDecision"), output)

    def test_capture_stages_candidate_without_mutating_canonical_memory(self) -> None:
        before = self.canonical_snapshot()
        self.capture("stop", "I decided to use bcrypt instead of plain sha256.")

        self.assertEqual(before, self.canonical_snapshot())
        candidates = self.pending_ids()
        self.assertEqual(1, len(candidates), candidates)

    def test_pending_candidate_requires_one_explicit_approval(self) -> None:
        message = "I decided to use bcrypt instead of plain sha256."
        self.capture("stop", message)
        candidate_id = self.pending_ids()[0]
        before_approval = self.canonical_snapshot()

        self.run_brain("review", str(self.repo), "--approve", candidate_id)
        after_first_approval = self.canonical_snapshot()
        self.assertNotEqual(before_approval, after_first_approval)
        self.assertIn("bcrypt", (self.brain_dir / "current-state.md").read_text(encoding="utf-8"))
        self.assertIn("bcrypt", (self.brain_dir / "log.md").read_text(encoding="utf-8"))

        # A replayed approval must not append a second copy or re-promote an
        # already consumed candidate.
        replay = self.run_brain(
            "review", str(self.repo), "--approve", candidate_id, check=False
        )
        self.assertNotEqual(
            0,
            replay.returncode,
            "a consumed candidate was accepted a second time",
        )
        self.assertEqual(after_first_approval, self.canonical_snapshot())
        self.assertEqual([], self.pending_ids())

    def test_duplicate_lifecycle_events_create_one_candidate(self) -> None:
        message = "I decided to use bcrypt instead of plain sha256."
        self.capture("precompact", message, session_id="same-session")
        self.capture("stop", message, session_id="same-session")
        self.capture("sessionend", message, session_id="same-session")

        candidates = self.pending_ids()
        self.assertEqual(1, len(candidates), candidates)

    def test_secret_from_hook_input_never_reaches_canonical_hot_memory(self) -> None:
        sentinel = "api_key=abcdefghijklmnop"
        before = self.canonical_snapshot()
        self.capture("stop", f"I decided {sentinel} is the production credential.")

        for path in (self.brain_dir / "current-state.md", self.brain_dir / "log.md"):
            self.assertNotIn(sentinel, path.read_text(encoding="utf-8"), path)
        candidate_id = self.pending_ids()[0]
        rejected = self.run_brain("review", str(self.repo), "--approve", candidate_id, check=False)
        self.assertNotEqual(0, rejected.returncode, "a redacted candidate was promoted")
        self.assertEqual(before, self.canonical_snapshot())

    def test_guard_handles_codex_apply_patch_payload_variants_and_windows_paths(self) -> None:
        # Codex has delivered both a raw patch string and an object containing
        # ``command``.  The path may use Windows separators even when the hook
        # command itself is evaluated elsewhere.
        raw_patch = "*** Begin Patch\n*** Update File: .brain/map.md\n@@\n-old\n+new\n*** End Patch"
        self.assert_guard_denies("apply_patch", raw_patch)

        windows_patch = (
            "*** Begin Patch\n"
            "*** Update File: C:\\work\\sample-repo\\.brain\\index.md\n"
            "@@\n-old\n+new\n*** End Patch"
        )
        self.assert_guard_denies("apply_patch", {"command": windows_patch})

        normal_source_patch = (
            "*** Begin Patch\n"
            "*** Update File: C:\\work\\sample-repo\\src\\auth.py\n"
            "@@\n-old\n+new\n*** End Patch"
        )
        self.assertEqual("", self.hook_guard("apply_patch", {"command": normal_source_patch}))

    def test_cold_start_injection_is_smaller_than_full_wiki_baseline(self) -> None:
        # Make the on-demand store deliberately much larger than hot memory.
        # It need not be indexed for this assertion: the baseline models the
        # naive strategy of injecting every Markdown page at session start.
        concept = self.brain_dir / "concepts" / "large-architecture.md"
        concept.parent.mkdir(exist_ok=True)
        concept.write_text(
            "# Large architecture\n\n" + ("Detailed on-demand knowledge. " * 1800),
            encoding="utf-8",
        )
        full_wiki_chars = sum(
            len(page.read_text(encoding="utf-8"))
            for page in self.brain_dir.rglob("*.md")
            if ".state" not in page.parts
        )
        result = self.run_brain(
            "session-start",
            hook_payload={
                "hook_event_name": "SessionStart",
                "source": "startup",
                "cwd": str(self.repo),
            },
        )
        response = json.loads(result.stdout)
        injected = response["hookSpecificOutput"]["additionalContext"]
        injected_chars = len(injected)

        self.assertLess(
            injected_chars,
            full_wiki_chars,
            f"hot injection ({injected_chars} chars) was not smaller than the "
            f"full-wiki baseline ({full_wiki_chars} chars)",
        )
        # The fixture is intentionally large enough that a pointer-based
        # design should demonstrate a material, not merely one-character, win.
        self.assertLessEqual(injected_chars * 4, full_wiki_chars)


    def test_index_routes_are_generated_idempotently_and_lintable(self) -> None:
        decision = self.brain_dir / "decisions" / "ADR-001-auth.md"
        concept = self.brain_dir / "concepts" / "authentication.md"
        decision.parent.mkdir(exist_ok=True)
        concept.parent.mkdir(exist_ok=True)
        decision.write_text("# ADR-001: Authentication\n\nUse bcrypt.\n", encoding="utf-8")
        concept.write_text("# Authentication\n\nAuth subsystem notes.\n", encoding="utf-8")

        self.run_brain("index", str(self.repo))
        first = (self.brain_dir / "index.md").read_text(encoding="utf-8")
        self.assertIn("<!-- context-forge:auto-routes:begin -->", first)
        self.assertIn("[ADR-001: Authentication](decisions/ADR-001-auth.md)", first)
        self.assertIn("[Authentication](concepts/authentication.md)", first)
        self.assertIn("Routing layer:", first, "human orientation text was overwritten")
        self.assertNotIn("orphan page", self.run_brain("lint", str(self.repo)).stdout)

        self.run_brain("index", str(self.repo))
        self.assertEqual(first, (self.brain_dir / "index.md").read_text(encoding="utf-8"))
        concept.unlink()
        self.run_brain("index", str(self.repo))
        updated = (self.brain_dir / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("concepts/authentication.md", updated)
        self.assertIn("Routing layer:", updated)

    def test_scan_context_and_registry_create_a_portable_baseline(self) -> None:
        self.run_brain("scan", str(self.repo))
        for relative in (
            "status.md", "registry.json", "requirements", "technical",
            "traceability", "questions", "audit",
        ):
            self.assertTrue((self.brain_dir / relative).exists(), relative)
        registry = json.loads((self.brain_dir / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual("2.0.0", registry["schema_version"])
        self.assertTrue(any(record["path"] == "technical/codebase-map.md" for record in registry["records"]))

        context = self.run_brain("context", str(self.repo), "authentication")
        self.assertIn(".brain/index.md", context.stdout)
        self.assertIn(".brain/status.md", context.stdout)

        maintenance = self.run_brain("maintain", str(self.repo), "--fix")
        self.assertIn("maintain clean", maintenance.stdout)

    def test_explicit_user_decisions_are_provenanced_but_code_is_not_intent(self) -> None:
        accepted = self.run_brain(
            "update", str(self.repo), "--kind", "decision",
            "--title", "Use bcrypt for password hashes",
            "--body", "New password hashes use bcrypt.",
            "--authority", "user_explicit",
            "--evidence", "User explicitly selected bcrypt during this task.",
            "--scope", "src/auth.py", "--accept",
        )
        self.assertIn("recorded ADR-001", accepted.stdout)
        decision = next((self.brain_dir / "decisions").glob("ADR-001-*.md"))
        content = decision.read_text(encoding="utf-8")
        self.assertIn("authority: user_explicit", content)
        self.assertIn("src/auth.py", content)
        trace = next((self.brain_dir / "traceability").glob("TRACE-001-*.md"))
        self.assertIn("ADR-001", trace.read_text(encoding="utf-8"))

        rejected = self.run_brain(
            "update", str(self.repo), "--kind", "requirement",
            "--title", "Invented scope", "--body", "Agent guessed this.",
            "--authority", "agent_inference", "--evidence", "No user message.", "--accept",
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("requires --authority user_explicit", rejected.stdout)

        technical = self.run_brain(
            "update", str(self.repo), "--kind", "technical",
            "--title", "Authentication currently returns true",
            "--body", "AuthManager.login currently returns True in the sample code.",
            "--authority", "code_observed", "--evidence", "src/auth.py:2-3",
            "--scope", "src/auth.py",
        )
        self.assertIn("recorded TECH-001", technical.stdout)

    def test_sync_records_only_code_observed_evidence(self) -> None:
        self.run_brain("sync", str(self.repo), "--path", "src/auth.py", "--apply")
        record = next((self.brain_dir / "technical").glob("TECH-*-observed-changes-*.md"))
        content = record.read_text(encoding="utf-8")
        self.assertIn("authority: code_observed", content)
        self.assertIn("does not assert product intent", content)
        self.assertIn("src/auth.py", content)

    def test_injection_markers_strip_feedback_and_suppress_only_first_turn(self) -> None:
        session_id = "marker-session"
        start = self.run_brain(
            "session-start",
            hook_payload={
                "hook_event_name": "SessionStart",
                "source": "startup",
                "cwd": str(self.repo),
                "session_id": session_id,
            },
        )
        start_context = json.loads(start.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("<!-- context-forge:begin -->", start_context)
        self.assertIn("<!-- context-forge:end -->", start_context)

        first_turn = self.run_brain(
            "turn-inject",
            hook_payload={
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(self.repo),
                "session_id": session_id,
                "prompt": "How does AuthManager login work?",
            },
        )
        self.assertEqual("", first_turn.stdout, "the first prompt duplicated cold-start routing")
        second_turn = self.run_brain(
            "turn-inject",
            hook_payload={
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(self.repo),
                "session_id": session_id,
                "prompt": "How does AuthManager login work?",
            },
        )
        self.assertIn("<!-- context-forge:begin -->", second_turn.stdout)

        compact = self.run_brain(
            "session-start",
            hook_payload={
                "hook_event_name": "SessionStart",
                "source": "compact",
                "cwd": str(self.repo),
                "session_id": session_id,
            },
        )
        compact_context = json.loads(compact.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Current State", compact_context)
        self.assertNotIn("Wiki pages available", compact_context)

        self.capture(
            "stop",
            "<!-- context-forge:begin --> I decided fake-memory must never persist. "
            "<!-- context-forge:end --> I decided real-memory is the approved choice.",
            session_id="feedback-session",
        )
        candidates = json.loads(self.run_brain("review", str(self.repo), "--pending").stdout)["candidates"]
        proposed = json.dumps(candidates)
        self.assertIn("real-memory", proposed)
        self.assertNotIn("fake-memory", proposed)

    def test_consolidation_requires_explicit_apply_and_reindexes_route(self) -> None:
        old_stamp = "2000-01-01T00:00:00Z"
        current_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log = self.brain_dir / "log.md"
        log.write_text(
            "# Log\n\n"
            f"## [{old_stamp}] review-approved candidate:old | deterministic\n"
            "- (decisions) old durable decision\n\n"
            f"## [{current_stamp}] review-approved candidate:new | deterministic\n"
            "- (decisions) current durable decision\n",
            encoding="utf-8",
        )
        before = self.canonical_snapshot()
        plan = self.run_brain("consolidate", str(self.repo))
        self.assertIn("--apply", plan.stdout)
        self.assertEqual(before, self.canonical_snapshot())
        self.assertFalse(list((self.brain_dir / "concepts").glob("log-summary-*.md")))

        self.run_brain("consolidate", str(self.repo), "--apply")
        summaries = list((self.brain_dir / "concepts").glob("log-summary-*.md"))
        self.assertEqual(1, len(summaries))
        self.assertIn("old durable decision", summaries[0].read_text(encoding="utf-8"))
        self.assertIn("current durable decision", log.read_text(encoding="utf-8"))
        self.assertIn(summaries[0].name, (self.brain_dir / "index.md").read_text(encoding="utf-8"))

        after_apply = self.canonical_snapshot()
        self.run_brain("consolidate", str(self.repo), "--apply")
        self.assertEqual(after_apply, self.canonical_snapshot())

    def test_semantic_review_stays_private_even_with_a_configured_llm(self) -> None:
        stub = Path(self._tmp.name) / "semantic_review_stub.py"
        stub.write_text(
            "import json\n"
            "print(json.dumps({'contradictions': ['overview vs map'], 'stale': [], 'gaps': ['auth tracing']}))\n",
            encoding="utf-8",
        )
        before = self.canonical_snapshot()
        result = self.run_brain(
            "review", str(self.repo), "--if-due",
            env={"BRAIN_LLM_CMD": json.dumps([sys.executable, str(stub), "{prompt}"])},
        )
        report = self.brain_dir / ".state" / "reviews" / "semantic-review.md"
        self.assertIn("review complete", result.stdout)
        self.assertTrue(report.is_file())
        self.assertIn("auth tracing", report.read_text(encoding="utf-8"))
        self.assertEqual(before, self.canonical_snapshot())

    def test_installer_renders_custom_engine_and_reconciles_idempotently(self) -> None:
        engine = self._tmp.name and Path(self._tmp.name) / "custom context-forge engine"
        assert engine is not None
        (engine / "scripts").mkdir(parents=True)
        preserved = engine / "scripts" / "local-note.txt"
        preserved.write_text("do not remove", encoding="utf-8")

        unrelated = {"matcher": "*", "hooks": [{"type": "command", "command": "echo unrelated"}]}
        old_context_forge = {
            "matcher": "*",
            "hooks": [{
                "type": "command",
                "command": "python3 \"$HOME/.context-forge/scripts/brain.py\" capture --event stop",
            }],
        }
        claude_settings = self.repo / ".claude" / "settings.json"
        codex_hooks = self.repo / ".codex" / "hooks.json"
        claude_settings.parent.mkdir(parents=True)
        codex_hooks.parent.mkdir(parents=True)
        claude_settings.write_text(json.dumps({"hooks": {"Stop": [unrelated, old_context_forge]}}), encoding="utf-8")
        codex_hooks.write_text(json.dumps({"hooks": {"Stop": [unrelated, old_context_forge]}}), encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# Existing project instructions\n", encoding="utf-8")

        command = [
            sys.executable, str(INSTALLER), "--harness", "all", "--scope", "project",
            "--repo", str(self.repo), "--engine-dir", str(engine),
        ]
        first = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        second = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertIn("already current", second.stdout)

        self.assertTrue((engine / "scripts" / "brain.py").is_file())
        self.assertTrue((engine / "templates" / "index.md").is_file())
        self.assertTrue((engine / "SKILL.md").is_file())
        self.assertTrue((engine / "AGENTS.md.snippet.md").is_file())
        self.assertEqual("do not remove", preserved.read_text(encoding="utf-8"))

        expected_events = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PreCompact", "Stop", "SubagentStop", "SessionEnd"}
        engine_marker = str(engine / "scripts" / "brain.py").replace("\\", "/").casefold()

        def owned_handlers(config: dict, event: str) -> list[dict]:
            found: list[dict] = []
            for group in config.get("hooks", {}).get(event, []):
                for handler in group.get("hooks", []):
                    blob = " ".join(str(handler.get(key, "")) for key in ("command", "commandWindows"))
                    normalized = blob.replace("\\", "/").casefold()
                    if engine_marker in normalized or "context-forge/scripts/brain.py" in normalized:
                        found.append(handler)
            return found

        for config_path in (claude_settings, codex_hooks):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(any(h.get("command") == "echo unrelated" for g in config["hooks"]["Stop"] for h in g["hooks"]))
            for event in expected_events:
                self.assertEqual(1, len(owned_handlers(config, event)), f"{config_path}: {event}")
            serialized = re.sub(r"/+", "/", json.dumps(config).replace("\\", "/").casefold())
            self.assertIn(engine_marker, serialized)
            self.assertNotIn("$home/.context-forge/scripts/brain.py", serialized)

        agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(1, agents.count("<!-- BEGIN context-forge -->"))
        self.assertIn("Existing project instructions", agents)
        self.assertIn(str(engine), agents)
        self.assertNotIn("~/.context-forge", (engine / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("/hooks", first.stdout)
        copilot = (self.repo / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
        claude_adapter = (self.repo / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(1, copilot.count("<!-- BEGIN context-forge -->"))
        self.assertIn("portable", copilot)
        self.assertEqual(1, claude_adapter.count("<!-- BEGIN context-forge -->"))
        self.assertIn("AGENTS.md", claude_adapter)

        # Exercise the exact Windows override written to Codex hooks rather
        # than merely inspecting its text. The custom engine path includes a
        # space, which catches quoting regressions as well as launcher issues.
        codex_config = json.loads(codex_hooks.read_text(encoding="utf-8"))
        windows_command = owned_handlers(codex_config, "SessionStart")[0]["commandWindows"]
        windows_run = subprocess.run(
            windows_command,
            input=json.dumps({
                "hook_event_name": "SessionStart",
                "source": "startup",
                "cwd": str(self.repo),
                "session_id": "windows-hook-smoke",
            }),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.repo),
            shell=True,
        )
        self.assertEqual(0, windows_run.returncode, windows_run.stdout + windows_run.stderr)
        self.assertIn("additionalContext", windows_run.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
