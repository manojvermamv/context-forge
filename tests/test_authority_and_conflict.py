from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import cmd_init
from context_forge.core.authority import (
    AuthorityDomain,
    AuthorityLevel,
    EpistemicAuthority,
    ResolutionDisposition,
)
from context_forge.compiler.conflict import ConflictResolver
from context_forge.knowledge.update import create_knowledge_record
from context_forge.store.paths import brain_paths, read_text


class AuthorityAndConflictTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cmd_init(self.tmp)
        self.p = brain_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_domain_pair_alone_does_not_imply_conflict(self):
        """Orthogonal or non-contradictory implementation claims must agree, not be marked stale."""
        claim1 = {
            "id": "TECH-001",
            "kind": "technical",
            "authority": "code_observed",
            "path": "src/auth.py",
            "details": "JWT token verification handler",
        }
        claim2 = {
            "id": "TECH-002",
            "kind": "technical",
            "authority": "code_observed",
            "path": "src/db.py",
            "details": "Postgres connection pooling",
        }
        res = EpistemicAuthority.resolve_claims(claim1, claim2)
        self.assertFalse(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.INCOMPARABLE)
        self.assertEqual(len(res.preserved_claims), 2)

    def test_intent_vs_implementation_drift(self):
        """When code reality bypasses authoritative intent, DRIFT is reported and both truths preserved."""
        intent = {
            "id": "REQ-010",
            "kind": "requirement",
            "authority": "user_explicit",
            "title": "Token Validation Required",
            "body": "All API endpoints must enforce token validation before dispatch.",
            "scope": ["src/api/router.py"],
        }
        impl = {
            "path": "src/api/router.py",
            "kind": "technical",
            "authority": "code_observed",
            "details": "Public router bypasses token validation for performance.",
        }
        res = EpistemicAuthority.resolve_claims(intent, impl)
        self.assertTrue(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.DRIFT)
        self.assertIsNone(res.winner)  # Intent remains standard, code remains reality
        self.assertEqual(len(res.preserved_claims), 2)
        self.assertTrue(res.reconciliation_required)

    def test_policy_vs_implementation_violation(self):
        """When code reality violates policy invariant, VIOLATION is reported."""
        policy = {
            "id": "POL-001",
            "kind": "policy",
            "authority": "policy_mandate",
            "title": "Security Invariant: Transport Encryption",
            "body": "All outgoing sockets must enforce TLS 1.3.",
            "scope": ["src/net/client.py"],
        }
        impl = {
            "path": "src/net/client.py",
            "kind": "technical",
            "authority": "code_observed",
            "details": "Client connects without TLS in debug build.",
        }
        res = EpistemicAuthority.resolve_claims(policy, impl)
        self.assertTrue(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.VIOLATION)

    def test_intent_vs_experience_subordinates_memory(self):
        """Intent strictly rejects contradictory advice from agent experience."""
        intent = {
            "id": "ADR-004",
            "kind": "decision",
            "authority": "user_explicit",
            "title": "Do not use Redis",
            "body": "Redis is rejected and forbidden due to operational cost.",
        }
        exp = {
            "source": "agentmemory",
            "authority": "procedural_memory",
            "finding": "Recommended using Redis cache for session lookup",
        }
        res = EpistemicAuthority.resolve_claims(intent, exp)
        self.assertTrue(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.ADVICE_REJECTED)
        self.assertEqual(res.winner, "ADR-004")

    def test_active_user_override(self):
        """Active user instruction supersedes past accepted ADR."""
        past_adr = {
            "id": "ADR-001",
            "kind": "decision",
            "authority": "user_explicit",
            "authority_level": AuthorityLevel.USER_EXPLICIT_ACCEPTED,
            "title": "Use SQLite",
            "body": "Use SQLite database for storage.",
        }
        current_user = {
            "id": "USER-PROMPT-CURRENT",
            "kind": "decision",
            "authority": "user_explicit",
            "authority_level": AuthorityLevel.USER_EXPLICIT_CURRENT,
            "title": "Migrate to PostgreSQL",
            "body": "Switch storage backend to PostgreSQL immediately.",
        }
        res = EpistemicAuthority.resolve_claims(past_adr, current_user)
        self.assertTrue(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.USER_OVERRIDE)
        self.assertEqual(res.winner, "USER-PROMPT-CURRENT")

    def test_explicit_supersession(self):
        """Explicit supersession links resolve cleanly."""
        adr_old = {
            "id": "ADR-002",
            "kind": "decision",
            "superseded_by": "ADR-008",
            "title": "V1 Protocol",
        }
        adr_new = {
            "id": "ADR-008",
            "kind": "decision",
            "title": "V2 Protocol",
        }
        res = EpistemicAuthority.resolve_claims(adr_old, adr_new)
        self.assertTrue(res.claims_conflict)
        self.assertEqual(res.disposition, ResolutionDisposition.SUPERSEDED)
        self.assertEqual(res.winner, "ADR-008")

    def test_policy_and_invariant_creation(self):
        """First-class policy and invariant records can be created in policies/ directory."""
        rc = create_knowledge_record(
            repo=self.tmp,
            kind="policy",
            title="Data Governance Invariant",
            body="No unhashed PII may be persisted.",
            authority="policy_mandate",
            evidence="Statutory regulatory requirement",
            scope=["src/users/storage.py"],
            accept=True,
        )
        self.assertEqual(rc, 0)

        pol_files = list(self.p["policies"].glob("POL-*.md"))
        self.assertEqual(len(pol_files), 1)
        text = read_text(pol_files[0])
        self.assertIn("id: POL-001", text)
        self.assertIn("kind: policy", text)
        self.assertIn("authority: policy_mandate", text)


if __name__ == "__main__":
    unittest.main()
