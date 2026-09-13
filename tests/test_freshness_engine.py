import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.traceability.freshness import (
    evaluate_record_freshness,
    check_record_freshness,
    GitComparisonStatus,
)


def _run_git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


class TestGitFreshnessEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        # Configure test git repo
        _run_git(self.repo, "init", "-b", "main")
        _run_git(self.repo, "config", "user.name", "Test Runner")
        _run_git(self.repo, "config", "user.email", "test@example.com")

        # Initial commit with files
        (self.repo / "src").mkdir()
        (self.repo / "src" / "auth.py").write_text("def auth(): pass\n", encoding="utf-8")
        (self.repo / "src" / "user.py").write_text("def user(): pass\n", encoding="utf-8")
        _run_git(self.repo, "add", ".")
        _run_git(self.repo, "commit", "-m", "Initial commit")
        self.sha1 = _run_git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_record(self, filename: str, commit_sha: str, linked_paths: list[str]) -> Path:
        p = self.repo / filename
        paths_str = "\n".join(f"- `{item}`" for item in linked_paths)
        content = f"""---
id: TECH-001
kind: technical
status: observed
authority: code_observed
commit_sha: {commit_sha}
---

# Technical Note

## Evidence

Code observation.

## Related paths

{paths_str}
"""
        p.write_text(content, encoding="utf-8")
        return p

    def test_clean_identical_state_is_fresh(self) -> None:
        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "fresh")
        self.assertEqual(eval_res.comparison_status, GitComparisonStatus.OK)

    def test_unrelated_file_modified_stays_fresh(self) -> None:
        # Modify src/user.py only
        (self.repo / "src" / "user.py").write_text("def user(): return 42\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/user.py")
        _run_git(self.repo, "commit", "-m", "Update user.py")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "fresh")

    def test_linked_file_committed_modification_is_possibly_stale(self) -> None:
        # Modify src/auth.py and commit
        (self.repo / "src" / "auth.py").write_text("def auth(): return True\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/auth.py")
        _run_git(self.repo, "commit", "-m", "Update auth.py")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "possibly_stale")
        self.assertIn("src/auth.py", eval_res.modified_paths)

    def test_linked_file_deleted_is_stale(self) -> None:
        # Delete src/auth.py and commit
        (self.repo / "src" / "auth.py").unlink()
        _run_git(self.repo, "rm", "src/auth.py")
        _run_git(self.repo, "commit", "-m", "Delete auth.py")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "stale")

    def test_linked_file_renamed_is_stale(self) -> None:
        # Rename src/auth.py to src/auth_v2.py
        _run_git(self.repo, "mv", "src/auth.py", "src/auth_v2.py")
        _run_git(self.repo, "commit", "-m", "Rename auth.py")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "stale")

    def test_dirty_worktree_is_possibly_stale(self) -> None:
        # Dirty uncommitted edit to src/auth.py
        (self.repo / "src" / "auth.py").write_text("def auth(): dirty\n", encoding="utf-8")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "possibly_stale")
        self.assertIn("src/auth.py", eval_res.dirty_paths)

    def test_exact_revert_returns_to_fresh(self) -> None:
        # Commit a change to auth.py
        (self.repo / "src" / "auth.py").write_text("def auth(): modified\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/auth.py")
        _run_git(self.repo, "commit", "-m", "Temp change")

        # Revert back to original content
        (self.repo / "src" / "auth.py").write_text("def auth(): pass\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/auth.py")
        _run_git(self.repo, "commit", "-m", "Revert to original")

        rec = self._create_record("tech.md", self.sha1, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertEqual(eval_res.status, "fresh")
        self.assertIn("Exact revert", eval_res.reason)

    def test_missing_observed_commit_never_fresh(self) -> None:
        fake_sha = "0" * 40
        rec = self._create_record("tech.md", fake_sha, ["src/auth.py"])
        eval_res = evaluate_record_freshness(self.repo, rec)
        self.assertNotEqual(eval_res.status, "fresh")
        self.assertEqual(eval_res.comparison_status, GitComparisonStatus.OBSERVED_COMMIT_MISSING)

    def test_non_git_directory_never_claims_git_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as plain_dir:
            p = Path(plain_dir)
            f = p / "foo.py"
            f.write_text("foo = 1\n", encoding="utf-8")
            rec = p / "rec.md"
            rec.write_text("---\nid: TECH-1\ncommit_sha: 1234\n---\n## Related paths\n- `foo.py`\n", encoding="utf-8")
            eval_res = evaluate_record_freshness(p, rec)
            self.assertEqual(eval_res.comparison_status, GitComparisonStatus.NOT_GIT_REPOSITORY)
            self.assertEqual(eval_res.status, "unverified")

    def test_divergent_branch_detected(self) -> None:
        # Create a branch and commit
        _run_git(self.repo, "checkout", "-b", "feature")
        (self.repo / "src" / "feature.py").write_text("feat = True\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/feature.py")
        _run_git(self.repo, "commit", "-m", "Feature commit")
        feat_sha = _run_git(self.repo, "rev-parse", "HEAD")

        # Switch back to main and commit something else
        _run_git(self.repo, "checkout", "main")
        (self.repo / "src" / "other.py").write_text("other = True\n", encoding="utf-8")
        _run_git(self.repo, "add", "src/other.py")
        _run_git(self.repo, "commit", "-m", "Main commit")

        # Record observed on feature branch, now checked against main
        rec = self._create_record("tech.md", feat_sha, ["src/feature.py"])
        eval_res = evaluate_record_freshness(self.repo, rec, current_head="HEAD")
        self.assertIn(eval_res.status, ("branch_diverged", "stale"))


if __name__ == "__main__":
    unittest.main()
