import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.core.models import (
    IdentityEnvelope,
    EvidenceStatement,
    KnowledgeRecord,
    ClaimProposition,
    ContextPack,
    now_iso,
    today,
)
from context_forge.core.identity import build_identity_envelope
from context_forge.store.markdown import parse_markdown_record, serialize_markdown_record
from tests.schema_validator import validate_json_schema, load_schema


class TestProvenanceAndSchemas(unittest.TestCase):
    def test_full_provenance_roundtrip(self) -> None:
        """Verify that all IdentityEnvelope and EvidenceStatement fields survive serialization to Markdown and back."""
        ident = IdentityEnvelope(
            schema_version="2.0",
            project_id="test-proj",
            repository="/repos/test-proj",
            commit_sha="a" * 40,
            branch="feature/hardening",
            worktree="/worktrees/feat",
            team_id="security-team",
            agent_id="sec-bot-9",
            agent_role="security_auditor",
            session_id="sess-999",
            conversation_id="conv-888",
            task_id="task-777",
            checkpoint_id="chk-666",
            producer="codebase-memory-mcp",
            producer_type="mcp_tool",
            producer_id="cbm-indexer",
            producer_version="0.10.8",
            reviewer="human-lead",
            harness="antigravity",
            model_provider="anthropic",
            model_id="claude-3-7-sonnet",
            authority_domain="POLICY",
            authority_level=85,
            observed_at="2026-09-13T12:00:00Z",
        )

        ev = EvidenceStatement(
            statement="Verified through test suite and AST inspection.",
            authority_level=85,
            source_type="test_result",
            source_refs=["tests/test_auth.py:45", "src/auth.py:12"],
            contains_redactions=False,
            digest="sha256:abcdef1234567890",
            observed_commit="a" * 40,
            producer="pytest",
            verification_state="verified",
            verified_at="2026-09-13T12:00:00Z",
        )

        record = KnowledgeRecord(
            id="POL-001",
            kind="policy",
            title="Authentication Token Lifetime Policy",
            status="accepted",
            authority="policy_mandate",
            updated=today(),
            created_at=now_iso(),
            body="Tokens must expire after exactly 15 minutes.",
            evidence=ev,
            identity=ident,
            confidence=0.99,
            scope=["src/auth.py", "src/tokens.py"],
            affected_symbols=["TokenValidator", "RefreshTokenHandler"],
            affected_tests=["tests/test_auth.py"],
            supersedes="ADR-002",
            superseded_by="",
            freshness="fresh",
            extra_frontmatter={"custom_metadata_tag": "high-priority"},
        )

        # 1. Serialize to Markdown
        md_text = serialize_markdown_record(record)
        self.assertIn("id: POL-001", md_text)
        self.assertIn("producer_type: mcp_tool", md_text)
        self.assertIn("commit_sha: " + ("a" * 40), md_text)
        self.assertIn("evidence_verification_state: verified", md_text)

        # 2. Parse back
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "POL-001-auth-policy.md"
            tmp_path.write_text(md_text, encoding="utf-8")
            parsed = parse_markdown_record(tmp_path, Path(tmpdir))

            self.assertEqual(parsed.id, record.id)
            self.assertEqual(parsed.kind, record.kind)
            self.assertEqual(parsed.title, record.title)
            self.assertEqual(parsed.status, record.status)
            self.assertEqual(parsed.authority, record.authority)
            self.assertEqual(parsed.body, record.body)
            self.assertEqual(parsed.scope, record.scope)
            self.assertEqual(parsed.affected_symbols, record.affected_symbols)
            self.assertEqual(parsed.affected_tests, record.affected_tests)
            self.assertEqual(parsed.supersedes, record.supersedes)
            self.assertEqual(parsed.confidence, record.confidence)

            # Identity envelope matches
            self.assertEqual(parsed.identity.commit_sha, ident.commit_sha)
            self.assertEqual(parsed.identity.project_id, ident.project_id)
            self.assertEqual(parsed.identity.branch, ident.branch)
            self.assertEqual(parsed.identity.worktree, ident.worktree)
            self.assertEqual(parsed.identity.agent_id, ident.agent_id)
            self.assertEqual(parsed.identity.agent_role, ident.agent_role)
            self.assertEqual(parsed.identity.producer, ident.producer)
            self.assertEqual(parsed.identity.producer_type, ident.producer_type)
            self.assertEqual(parsed.identity.producer_version, ident.producer_version)
            self.assertEqual(parsed.identity.reviewer, ident.reviewer)
            self.assertEqual(parsed.identity.harness, ident.harness)
            self.assertEqual(parsed.identity.model_provider, ident.model_provider)
            self.assertEqual(parsed.identity.model_id, ident.model_id)
            self.assertEqual(parsed.identity.authority_domain, ident.authority_domain)
            self.assertEqual(parsed.identity.authority_level, ident.authority_level)

            # Evidence metadata matches
            self.assertEqual(parsed.evidence.statement, ev.statement)
            self.assertEqual(parsed.evidence.source_type, ev.source_type)
            self.assertEqual(parsed.evidence.source_refs, ev.source_refs)
            self.assertEqual(parsed.evidence.digest, ev.digest)
            self.assertEqual(parsed.evidence.observed_commit, ev.observed_commit)
            self.assertEqual(parsed.evidence.producer, ev.producer)
            self.assertEqual(parsed.evidence.verification_state, ev.verification_state)

            # Extra frontmatter preserved
            self.assertEqual(parsed.extra_frontmatter.get("custom_metadata_tag"), "high-priority")

    def test_legacy_record_backwards_compatibility(self) -> None:
        """Verify that older records without rich provenance parse cleanly without crashing."""
        legacy_md = """---
id: ADR-001
status: accepted
authority: user_explicit
updated: 2026-01-01
---

# Use PostgreSQL

## Decision

We decided to use PostgreSQL.

## Evidence

Customer database performance review.

## Related paths

- `db/schema.sql`
"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "ADR-001-use-postgres.md"
            p.write_text(legacy_md, encoding="utf-8")
            rec = parse_markdown_record(p, Path(tmpdir))

            self.assertEqual(rec.id, "ADR-001")
            self.assertEqual(rec.kind, "decision")
            self.assertEqual(rec.status, "accepted")
            self.assertEqual(rec.authority, "user_explicit")
            self.assertEqual(rec.scope, ["db/schema.sql"])
            self.assertEqual(rec.evidence.statement, "Customer database performance review.")
            # Default empty identity, not crashed
            self.assertEqual(rec.identity.agent_id, "")
            self.assertEqual(rec.identity.commit_sha, "")

    def test_no_false_provenance_defaults(self) -> None:
        """Verify build_identity_envelope does not invent default_agent, developer, 70, etc."""
        ident = build_identity_envelope()
        self.assertEqual(ident.agent_id, "")
        self.assertEqual(ident.agent_role, "")
        self.assertEqual(ident.team_id, "")
        self.assertEqual(ident.producer_version, "")
        self.assertEqual(ident.authority_level, 0)

    def test_schema_validations(self) -> None:
        """Validate that to_dict() of all models conforms to the JSON schemas."""
        # 1. Identity Schema
        ident = IdentityEnvelope(
            schema_version="2.0",
            project_id="test",
            agent_role="architect",
            producer_type="agent",
            authority_domain="INTENT",
            authority_level=90,
        )
        ident_schema = load_schema("identity-envelope.schema.json")
        validate_json_schema(ident.to_dict(), ident_schema)

        # 2. Evidence Schema
        ev = EvidenceStatement(
            statement="Validated via test",
            authority_level=80,
            source_type="test_result",
            source_refs=["test.py:10"],
            verification_state="verified",
        )
        ev_schema = load_schema("evidence.schema.json")
        validate_json_schema(ev.to_dict(), ev_schema)

        # 3. Record Schema
        rec = KnowledgeRecord(
            id="REQ-101",
            kind="requirement",
            title="Fast Execution",
            status="accepted",
            authority="user_explicit",
            evidence=ev,
            identity=ident,
        )
        rec_schema = load_schema("record.schema.json")
        validate_json_schema(rec.to_dict(), rec_schema)

        # 4. ContextPack Schema
        pack = ContextPack(
            task="Optimize tokens",
            compiled_at=now_iso(),
            total_chars=1200,
            estimated_tokens=315,
            budget_chars=4000,
            authoritative_intent=[{"id": "REQ-101", "title": "Fast Execution"}],
            next_reading=["src/core.py"],
            provider_diagnostics=["Code intelligence: Native mapper."],
        )
        pack_schema = load_schema("context-pack.schema.json")
        validate_json_schema(pack.to_dict(), pack_schema)

    def test_adversarial_evidence_roundtrip_independent_of_identity(self) -> None:
        """Adversarial test: evidence authority != identity authority, distinct timestamps,
        contains_redactions=True, and verification_state=verified must all survive round-trip distinctly."""
        ident = IdentityEnvelope(
            schema_version="2.0",
            project_id="adversarial-proj",
            authority_domain="POLICY",
            authority_level=95,
            created_at="2026-09-10T08:00:00Z",
            observed_at="2026-09-10T08:00:00Z",
        )

        ev = EvidenceStatement(
            statement="Independent benchmark report showing latency regression.",
            authority_level=55,
            source_type="benchmark_run",
            source_refs=["benchmarks/run_99.json"],
            contains_redactions=True,
            digest="sha256:1122334455667788",
            observed_commit="b" * 40,
            producer="perf-harness",
            verification_state="verified",
            verified_at="2026-09-12T15:30:00Z",
            created_at="2026-09-12T15:00:00Z",
        )

        record = KnowledgeRecord(
            id="POL-099",
            kind="policy",
            title="Adversarial Evidence Decoupling Policy",
            status="accepted",
            authority="policy_mandate",
            updated="2026-09-13",
            created_at="2026-09-10T08:00:00Z",
            body="Policy statement with independent evidence.",
            evidence=ev,
            identity=ident,
            scope=["benchmarks/run_99.json"],
        )

        md_text = serialize_markdown_record(record)
        self.assertIn("authority_level: 95", md_text)
        self.assertIn("evidence_authority_level: 55", md_text)
        self.assertIn("evidence_contains_redactions: true", md_text)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "POL-099-adversarial.md"
            tmp_path.write_text(md_text, encoding="utf-8")
            parsed = parse_markdown_record(tmp_path, Path(tmpdir))

            # Identity envelope authority
            self.assertEqual(parsed.identity.authority_level, 95)
            self.assertEqual(parsed.identity.created_at, "2026-09-10T08:00:00Z")

            # Evidence authority must be 55, NOT 95!
            self.assertEqual(parsed.evidence.authority_level, 55)
            self.assertNotEqual(parsed.evidence.authority_level, parsed.identity.authority_level)

            # Timestamps must remain distinct
            self.assertEqual(parsed.evidence.created_at, "2026-09-12T15:00:00Z")
            self.assertEqual(parsed.evidence.verified_at, "2026-09-12T15:30:00Z")
            self.assertNotEqual(parsed.identity.created_at, parsed.evidence.created_at)

            # Redactions and verification state
            self.assertTrue(parsed.evidence.contains_redactions)
            self.assertEqual(parsed.evidence.verification_state, "verified")
            self.assertEqual(parsed.evidence.digest, "sha256:1122334455667788")
            self.assertEqual(parsed.evidence.producer, "perf-harness")

    def test_legacy_record_does_not_copy_identity_authority_to_evidence(self) -> None:
        """Legacy records without explicit evidence_authority_level must retain default 0,
        and not inherit identity.authority_level."""
        legacy_md = """---
id: REQ-050
status: accepted
authority: user_explicit
authority_level: 90
updated: 2026-01-01
---

# High Security Requirement

## Details

High security is required.

## Evidence

Internal security checklist.
"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "REQ-050-security.md"
            p.write_text(legacy_md, encoding="utf-8")
            rec = parse_markdown_record(p, Path(tmpdir))

            self.assertEqual(rec.identity.authority_level, 90)
            # Evidence authority must be safe default (0), not silently copy identity authority 90!
            self.assertEqual(rec.evidence.authority_level, 0)

    def test_claim_proposition_full_roundtrip(self) -> None:
        """Verify that ClaimProposition qualifiers, scope, and confidence survive Markdown roundtrip."""
        claim = ClaimProposition(
            subject="RiskGate",
            predicate="owner",
            object="planner",
            polarity=True,
            claim_key="RISK_001",
            qualifiers={"mode": "strict", "retry_count": 3},
            scope=["src/risk.py", "src/auth.py"],
            confidence=0.85,
        )
        record = KnowledgeRecord(
            id="REQ-CLAIM-01",
            kind="requirement",
            title="RiskGate Configuration Proposition",
            status="accepted",
            authority="user_explicit",
            claim=claim,
            scope=["src/risk.py", "src/auth.py"],
        )

        md_text = serialize_markdown_record(record)
        self.assertIn("claim_subject: RiskGate", md_text)
        self.assertIn("claim_predicate: owner", md_text)
        self.assertIn("claim_object: planner", md_text)
        self.assertIn("claim_confidence: 0.85", md_text)
        self.assertIn("claim_qualifiers:", md_text)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "REQ-CLAIM-01.md"
            p.write_text(md_text, encoding="utf-8")
            rec = parse_markdown_record(p, Path(tmpdir))

            self.assertIsNotNone(rec.claim)
            assert rec.claim is not None
            self.assertEqual(rec.claim.subject, "RiskGate")
            self.assertEqual(rec.claim.predicate, "owner")
            self.assertEqual(rec.claim.object, "planner")
            self.assertTrue(rec.claim.polarity)
            self.assertEqual(rec.claim.claim_key, "RISK_001")
            self.assertEqual(rec.claim.confidence, 0.85)
            self.assertEqual(rec.claim.scope, ["src/risk.py", "src/auth.py"])
            self.assertEqual(rec.claim.qualifiers, {"mode": "strict", "retry_count": 3})


if __name__ == "__main__":
    unittest.main()
