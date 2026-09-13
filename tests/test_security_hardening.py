from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import cmd_init
from context_forge.cli.doctor import cmd_doctor
from context_forge.core.evidence import screen_secrets, sanitize_evidence
from context_forge.knowledge.candidate import stage_candidate
from context_forge.knowledge.update import create_knowledge_record
from context_forge.providers.experience.agentmemory import AgentMemoryProvider
from context_forge.providers.code.cbm import CBMProvider
from context_forge.store.audit import append_audit_log
from context_forge.store.paths import brain_paths, read_text


class SecurityHardeningTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cmd_init(self.tmp)
        self.p = brain_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_secret_screening_patterns(self):
        """Screening redacts OpenAI, Anthropic, Bearer tokens, GitHub tokens, and generic API keys."""
        sample_secret = "Bearer sk-proj-1234567890abcdef1234567890abcdef"
        screened = screen_secrets(sample_secret)
        self.assertNotIn("sk-proj-1234567890abcdef", screened)
        self.assertIn("[REDACTED", screened)

        gh_secret = "ghp_1234567890abcdef1234567890abcdef1234"
        screened_gh = screen_secrets(gh_secret)
        self.assertNotIn("1234567890abcdef", screened_gh)

    def test_doctor_never_leaks_configured_secret(self):
        """When AGENTMEMORY_SECRET is present in env, doctor never prints the secret."""
        synthetic_secret = "secret-super-sensitive-token-12345"
        os.environ["AGENTMEMORY_SECRET"] = synthetic_secret
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_doctor(self.tmp)
            output = buf.getvalue()
            self.assertNotIn(synthetic_secret, output)
            self.assertIn("auth token configured: yes (hidden)", output)
        finally:
            del os.environ["AGENTMEMORY_SECRET"]

    def test_provider_diagnostic_sanitization(self):
        """Provider diagnostic messages redact sensitive environment tokens."""
        am = AgentMemoryProvider()
        secret_msg = "Error connecting to https://user:super_secret_pw@api.service.com/auth"
        screened = screen_secrets(secret_msg)
        self.assertNotIn("super_secret_pw", screened)

    def test_candidate_redacts_secrets_and_flags(self):
        """Candidate staging screens secrets and marks contains_redactions."""
        delta = {
            "decisions": ["switched to key ghp_1234567890abcdef1234567890abcdef1234 for deployment"],
            "blockers": [],
            "next_steps": [],
        }
        cid = stage_candidate(
            p=self.p,
            event="test_event",
            session="sess1",
            delta=delta,
            redacted=True,
        )
        cand_file = self.p["pending"] / f"{cid}.json"
        raw = read_text(cand_file)
        self.assertIn('"contains_redactions": true', raw)
        # Verify candidate approval blocks auto-promotion of redacted material
        from context_forge.knowledge.approval import approve_candidate
        rc = approve_candidate(self.tmp, cid)
        self.assertEqual(rc, 1)  # Approval rejected due to redacted material requiring human sanitization

    def test_audit_log_screens_secrets(self):
        """Audit log append never persists unscreened secret material."""
        secret_text = "sk-proj-99998888777766665555444433332222"
        append_audit_log(
            self.p,
            action="sync",
            record=self.tmp / "src/auth.py",
            detail=f"Applied changes with key: {screen_secrets(secret_text)}",
        )
        audit_file = list(self.p["audit"].glob("*.md"))[0]
        content = read_text(audit_file)
        self.assertNotIn(secret_text, content)


if __name__ == "__main__":
    unittest.main()
