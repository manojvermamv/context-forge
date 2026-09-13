#!/usr/bin/env python3
"""Unit tests for Context Forge v2 modular components."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Add src to sys.path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.core.authority import AuthorityLevel, EpistemicAuthority
from context_forge.core.evidence import screen_secrets, sanitize_evidence
from context_forge.core.models import (
    KnowledgeRecord,
    IdentityEnvelope,
    EvidenceStatement,
    ContextPack,
)
from context_forge.core.identity import build_identity_envelope
from context_forge.compiler.conflict import ConflictResolver
from context_forge.compiler.pack import compile_context_pack
from context_forge.traceability.freshness import check_record_freshness
from context_forge.providers.code import NativeCodeProvider, CodebaseMemoryMCPProvider
from context_forge.providers.experience import NativeExperienceProvider, AgentMemoryProvider
from context_forge.cli.commands import cmd_init


class ContextForgeV2ModuleTests(unittest.TestCase):

    def test_authority_hierarchy(self) -> None:
        self.assertGreater(
            EpistemicAuthority.get_level("user_explicit"),
            EpistemicAuthority.get_level("code_observed"),
        )
        self.assertGreater(
            EpistemicAuthority.get_level("code_observed"),
            EpistemicAuthority.get_level("procedural_memory"),
        )
        self.assertGreater(
            EpistemicAuthority.get_level("procedural_memory"),
            EpistemicAuthority.get_level("agent_inference"),
        )

        # ADR without user_explicit authority must be rejected
        can_accept, reason = EpistemicAuthority.can_accept_intent("decision", "agent_inference", accept=True)
        self.assertFalse(can_accept)
        self.assertIn("user_explicit", reason or "")

        # ADR without accept flag must be rejected
        can_accept, reason = EpistemicAuthority.can_accept_intent("decision", "user_explicit", accept=False)
        self.assertFalse(can_accept)
        self.assertIn("accept", reason or "")

        # Valid ADR
        can_accept, _ = EpistemicAuthority.can_accept_intent("decision", "user_explicit", accept=True)
        self.assertTrue(can_accept)

    def test_evidence_sanitization_and_secrets(self) -> None:
        # Secret screening
        raw_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz12"
        screened = screen_secrets(f"My token is {raw_token}")
        self.assertNotIn(raw_token, screened)
        self.assertIn("[REDACTED]", screened)

        # Sanitize evidence raises error on secrets
        with self.assertRaises(ValueError):
            sanitize_evidence("token = 'sk-1234567890abcdefghijklmnopqrst'")

        # Clean evidence passes
        clean = sanitize_evidence("User explicitly chose PostgreSQL over MongoDB.")
        self.assertEqual(clean, "User explicitly chose PostgreSQL over MongoDB.")

    def test_identity_envelope(self) -> None:
        envelope = build_identity_envelope(
            payload={"session_id": "sess-42", "agent_id": "architect-agent"},
            agent_role="architect",
            task_id="task-999",
        )
        self.assertEqual(envelope.session_id, "sess-42")
        self.assertEqual(envelope.agent_id, "architect-agent")
        self.assertEqual(envelope.agent_role, "architect")
        self.assertEqual(envelope.task_id, "task-999")

        d = envelope.to_dict()
        restored = IdentityEnvelope.from_dict(d)
        self.assertEqual(restored.session_id, "sess-42")

    def test_conflict_resolver(self) -> None:
        authoritative = [
            {
                "id": "ADR-041",
                "title": "Redis explicitly rejected for distributed state",
                "body": "Redis rejected in favor of PostgreSQL advisory locks.",
            }
        ]
        experience = [
            {
                "finding": "Use redis for caching and distributed locks",
                "source": "agentmemory",
            }
        ]
        conflicts = ConflictResolver.detect_conflicts(authoritative, experience)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("Conflict detected", conflicts[0]["warning"])
        self.assertEqual(conflicts[0]["winner"], "ADR-041")

    def test_freshness_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir(parents=True)
            source_file = repo / "src" / "worker.py"
            source_file.write_text("def work(): pass\n", encoding="utf-8")

            record_path = repo / "tech.md"
            record_path.write_text(
                "---\nid: TECH-001\n---\n## Related paths\n- `src/worker.py`\n",
                encoding="utf-8",
            )

            # Initially fresh when not in changed files
            freshness = check_record_freshness(repo, record_path, changed_files=set())
            self.assertEqual(freshness, "fresh")

            # Stale when file is modified in changed files
            freshness = check_record_freshness(repo, record_path, changed_files={"src/worker.py"})
            self.assertEqual(freshness, "possibly_stale")

            # Stale when linked file is deleted
            source_file.unlink()
            freshness = check_record_freshness(repo, record_path, changed_files=set())
            self.assertEqual(freshness, "stale")

    def test_federated_context_pack_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            cmd_init(repo)

            pack = compile_context_pack(repo, task_query="authentication password hashing")
            self.assertIsInstance(pack, ContextPack)
            rendered = pack.to_text()
            self.assertIn("# Context Pack — Task: authentication password hashing", rendered)
            self.assertIn("Next Reading", rendered)
            self.assertGreater(pack.total_chars, 0)
            self.assertLessEqual(pack.total_chars, pack.budget_chars * 2)

    def test_provider_fallbacks(self) -> None:
        native_code = NativeCodeProvider()
        self.assertTrue(native_code.is_available())
        self.assertEqual(native_code.name(), "native_code_map")

        cbm = CodebaseMemoryMCPProvider()
        # Without CBM server running, is_available is False without raising exception
        self.assertFalse(cbm.is_available())
        self.assertEqual(cbm.query_impact("test", []), [])

        am = AgentMemoryProvider()
        # Without AgentMemory running, is_available is False without raising exception
        self.assertFalse(am.is_available())
        self.assertEqual(am.recall_lessons("test"), [])


if __name__ == "__main__":
    unittest.main()
