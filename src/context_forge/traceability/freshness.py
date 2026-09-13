from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from context_forge.store.paths import brain_paths, read_text, atomic_write
from context_forge.store.lock import repo_lock
from context_forge.core.identity import get_git_info


@dataclass
class WorktreeStatusResult:
    """Result of querying git status in working tree."""
    status: str  # "OK", "NOT_GIT_REPOSITORY", "GIT_ERROR"
    paths: list[str] = field(default_factory=list)
    diagnostic: str = ""


class GitComparisonStatus(str, Enum):
    """Result of attempting to compare repository state against an observed Git commit."""
    OK = "OK"
    NOT_GIT_REPOSITORY = "NOT_GIT_REPOSITORY"
    OBSERVED_COMMIT_MISSING = "OBSERVED_COMMIT_MISSING"
    SHALLOW_HISTORY_BOUNDARY = "SHALLOW_HISTORY_BOUNDARY"
    BRANCH_DIVERGED = "BRANCH_DIVERGED"
    DETACHED_HEAD = "DETACHED_HEAD"
    GIT_ERROR = "GIT_ERROR"
    UNVERIFIED = "UNVERIFIED"


@dataclass
class GitDiffResult:
    """Structured delta and comparison outcome between an observed commit and current HEAD."""
    status: GitComparisonStatus
    modified: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    renamed: set[str] = field(default_factory=set)
    diverged: bool = False
    error_message: str = ""


@dataclass
class FreshnessEvaluation:
    """Complete diagnostic assessment of a record's living freshness."""
    record_id: str
    status: str  # fresh, possibly_stale, stale, contradicted, unverified, branch_diverged, superseded, not_applicable
    comparison_status: GitComparisonStatus
    observed_commit: str = ""
    current_head: str = ""
    linked_paths: list[str] = field(default_factory=list)
    dirty_paths: list[str] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    renamed_paths: list[str] = field(default_factory=list)
    reason: str = ""


def get_git_changed_paths_result(repo: Path) -> WorktreeStatusResult:
    """Query uncommitted modified, untracked, or deleted paths with failure semantics."""
    if not (repo / ".git").exists():
        return WorktreeStatusResult(
            status="NOT_GIT_REPOSITORY",
            paths=[],
            diagnostic="Not a git repository.",
        )
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return WorktreeStatusResult(
                status="GIT_ERROR",
                paths=[],
                diagnostic=f"git status failed with exit code {result.returncode}: {result.stderr.strip()}",
            )
        paths = []
        for line in result.stdout.splitlines():
            if len(line) >= 4:
                paths.append(line[3:].split(" -> ")[-1].replace("\\", "/"))
        return WorktreeStatusResult(
            status="OK",
            paths=sorted(set(paths)),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return WorktreeStatusResult(
            status="GIT_ERROR",
            paths=[],
            diagnostic=f"Failed to execute git status: {exc}",
        )


def get_git_changed_paths(repo: Path) -> list[str]:
    """Return uncommitted modified, untracked, or deleted paths in working tree."""
    return get_git_changed_paths_result(repo).paths


def is_detached_head(repo: Path) -> bool:
    """Check if repository HEAD is detached."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "-q", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode != 0
    except (subprocess.SubprocessError, OSError):
        return False


def get_git_diff_result(
    repo: Path,
    base_sha: str,
    target_sha: str = "HEAD",
) -> GitDiffResult:
    """Perform rigorous structured Git comparison between base_sha and target_sha."""
    if not (repo / ".git").exists():
        return GitDiffResult(
            status=GitComparisonStatus.NOT_GIT_REPOSITORY,
            error_message="Repository does not have a .git directory.",
        )

    clean_sha = base_sha.strip()
    if not clean_sha:
        return GitDiffResult(
            status=GitComparisonStatus.UNVERIFIED,
            error_message="No observed commit SHA specified.",
        )

    # 1. Verify that base_sha actually exists in object database
    check_obj = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{clean_sha}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check_obj.returncode != 0:
        # Check if shallow clone boundary
        is_shallow = (repo / ".git" / "shallow").exists()
        if is_shallow:
            return GitDiffResult(
                status=GitComparisonStatus.SHALLOW_HISTORY_BOUNDARY,
                error_message=f"Commit {clean_sha} not present due to shallow clone history.",
            )
        return GitDiffResult(
            status=GitComparisonStatus.OBSERVED_COMMIT_MISSING,
            error_message=f"Observed commit {clean_sha} does not exist in local Git object database.",
        )

    # 2. Check merge base / ancestor relationship
    merge_base = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", clean_sha, target_sha],
        capture_output=True,
        text=True,
        check=False,
    )
    is_ancestor = (merge_base.returncode == 0)

    # If not ancestor, verify if there is a common merge base (branch diverged)
    if not is_ancestor:
        common_base = subprocess.run(
            ["git", "-C", str(repo), "merge-base", clean_sha, target_sha],
            capture_output=True,
            text=True,
            check=False,
        )
        if common_base.returncode == 0 and common_base.stdout.strip():
            # Diverged branches
            res = subprocess.run(
                ["git", "-C", str(repo), "diff", "--name-status", f"{clean_sha}...{target_sha}"],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if res.returncode == 0:
                mod, dlt, rnm = _parse_name_status_output(res.stdout)
                return GitDiffResult(
                    status=GitComparisonStatus.BRANCH_DIVERGED,
                    modified=mod,
                    deleted=dlt,
                    renamed=rnm,
                    diverged=True,
                    error_message=f"History diverged between {clean_sha} and {target_sha}.",
                )
            return GitDiffResult(
                status=GitComparisonStatus.BRANCH_DIVERGED,
                diverged=True,
                error_message=f"Branches diverged between {clean_sha} and {target_sha}.",
            )

    # 3. Direct linear diff
    res = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", f"{clean_sha}..{target_sha}"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if res.returncode != 0:
        return GitDiffResult(
            status=GitComparisonStatus.GIT_ERROR,
            error_message=f"git diff failed with exit code {res.returncode}: {res.stderr.strip()}",
        )

    mod, dlt, rnm = _parse_name_status_output(res.stdout)
    return GitDiffResult(
        status=GitComparisonStatus.OK,
        modified=mod,
        deleted=dlt,
        renamed=rnm,
    )


def _parse_name_status_output(stdout: str) -> tuple[set[str], set[str], set[str]]:
    modified, deleted, renamed = set(), set(), set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("D"):
            deleted.add(parts[-1].replace("\\", "/"))
        elif status.startswith("R"):
            if len(parts) >= 3:
                renamed.add(parts[1].replace("\\", "/"))
                renamed.add(parts[2].replace("\\", "/"))
            else:
                renamed.add(parts[-1].replace("\\", "/"))
        else:
            modified.add(parts[-1].replace("\\", "/"))
    return modified, deleted, renamed


def extract_record_commit(record_text: str) -> str:
    """Extract observed commit SHA from frontmatter."""
    m = re.search(r"(?m)^commit_sha:\s*([a-fA-F0-9]+)", record_text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?m)^evidence_observed_commit:\s*([a-fA-F0-9]+)", record_text)
    if m2:
        return m2.group(1).strip()
    m3 = re.search(r"(?m)^observed_commit:\s*([a-fA-F0-9]+)", record_text)
    if m3:
        return m3.group(1).strip()
    return ""


def check_exact_revert(repo: Path, observed_sha: str, path: str) -> bool:
    """Check if current file content on disk exactly matches the blob at observed_sha."""
    if not (repo / path).exists():
        return False
    try:
        res_obs = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{observed_sha}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_obs.returncode != 0:
            return False
        obs_hash = res_obs.stdout.strip()

        # Hash current file on disk as Git blob
        res_cur = subprocess.run(
            ["git", "-C", str(repo), "hash-object", str(repo / path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_cur.returncode != 0:
            return False
        cur_hash = res_cur.stdout.strip()
        return obs_hash == cur_hash
    except (subprocess.SubprocessError, OSError):
        return False


def evaluate_record_freshness(
    repo: Path,
    record_path: Path,
    changed_files: Optional[set[str]] = None,
    current_head: str = "HEAD",
) -> FreshnessEvaluation:
    """Evaluate record freshness with full Git provenance evidence and failure semantics."""
    text = read_text(record_path)
    rec_id_match = re.search(r"(?m)^id:\s*([A-Za-z0-9_-]+)", text)
    rec_id = rec_id_match.group(1) if rec_id_match else record_path.stem

    # Find all path references
    linked_paths = sorted(set(re.findall(r"- `([^`]+\.[a-zA-Z0-9]+)`", text)))
    if not linked_paths:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="fresh",
            comparison_status=GitComparisonStatus.OK,
            reason="No linked paths to verify.",
        )

    # 1. Verify existence on disk
    missing = [lp for lp in linked_paths if not (repo / lp).exists()]
    if missing:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="stale",
            comparison_status=GitComparisonStatus.OK,
            linked_paths=linked_paths,
            deleted_paths=missing,
            reason=f"Linked path(s) deleted from disk: {missing}",
        )

    # 2. Check uncommitted working tree dirty files
    if changed_files is None:
        wt_res = get_git_changed_paths_result(repo)
        if wt_res.status == "GIT_ERROR":
            return FreshnessEvaluation(
                record_id=rec_id,
                status="unverified",
                comparison_status=GitComparisonStatus.GIT_ERROR,
                linked_paths=linked_paths,
                reason=f"Working tree dirty state could not be verified: {wt_res.diagnostic}",
            )
        dirty = set(wt_res.paths)
    else:
        dirty = set(changed_files)

    dirty_overlap = sorted(dirty.intersection(linked_paths))
    if dirty_overlap:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="possibly_stale",
            comparison_status=GitComparisonStatus.OK,
            linked_paths=linked_paths,
            dirty_paths=dirty_overlap,
            reason=f"Linked path(s) have uncommitted working tree modifications: {dirty_overlap}",
        )

    # 3. Check commit-anchored diff
    observed_sha = extract_record_commit(text)
    if not observed_sha:
        # In git repo with linked files, missing commit anchor is UNVERIFIED
        if (repo / ".git").exists():
            return FreshnessEvaluation(
                record_id=rec_id,
                status="unverified",
                comparison_status=GitComparisonStatus.UNVERIFIED,
                linked_paths=linked_paths,
                reason="Record has linked code paths but lacks durable commit anchor.",
            )
        return FreshnessEvaluation(
            record_id=rec_id,
            status="fresh",
            comparison_status=GitComparisonStatus.NOT_GIT_REPOSITORY,
            linked_paths=linked_paths,
            reason="Non-git repository; files exist on disk.",
        )

    diff_res = get_git_diff_result(repo, observed_sha, current_head)

    # Handle Git comparison failure semantics explicitly - NEVER default to fresh on failure!
    if diff_res.status == GitComparisonStatus.NOT_GIT_REPOSITORY:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="unverified",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            reason=diff_res.error_message,
        )

    if diff_res.status in (GitComparisonStatus.OBSERVED_COMMIT_MISSING, GitComparisonStatus.SHALLOW_HISTORY_BOUNDARY):
        return FreshnessEvaluation(
            record_id=rec_id,
            status="stale" if diff_res.status == GitComparisonStatus.OBSERVED_COMMIT_MISSING else "unverified",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            reason=diff_res.error_message,
        )

    if diff_res.status == GitComparisonStatus.BRANCH_DIVERGED:
        overlap_mod = sorted(diff_res.modified.intersection(linked_paths))
        overlap_del = sorted(diff_res.deleted.intersection(linked_paths))
        if overlap_del:
            return FreshnessEvaluation(
                record_id=rec_id,
                status="stale",
                comparison_status=diff_res.status,
                observed_commit=observed_sha,
                linked_paths=linked_paths,
                deleted_paths=overlap_del,
                reason=f"Branch diverged and linked paths deleted: {overlap_del}",
            )
        return FreshnessEvaluation(
            record_id=rec_id,
            status="branch_diverged",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            modified_paths=overlap_mod,
            reason=f"Observed commit is on a diverged branch: {diff_res.error_message}",
        )

    if diff_res.status == GitComparisonStatus.GIT_ERROR:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="unverified",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            reason=f"Git comparison error: {diff_res.error_message}",
        )

    # Status == OK: evaluate deltas
    del_overlap = sorted(diff_res.deleted.intersection(linked_paths))
    ren_overlap = sorted(diff_res.renamed.intersection(linked_paths))
    if del_overlap or ren_overlap:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="stale",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            deleted_paths=del_overlap,
            renamed_paths=ren_overlap,
            reason=f"Linked paths deleted or renamed in Git history: {del_overlap + ren_overlap}",
        )

    mod_overlap = sorted(diff_res.modified.intersection(linked_paths))
    if mod_overlap:
        # Check exact-revert: if disk content for all modified files is identical to observed_sha
        all_reverted = all(check_exact_revert(repo, observed_sha, p) for p in mod_overlap)
        if all_reverted:
            return FreshnessEvaluation(
                record_id=rec_id,
                status="fresh",
                comparison_status=diff_res.status,
                observed_commit=observed_sha,
                linked_paths=linked_paths,
                reason="Exact revert: content on disk matches observed commit state.",
            )
        return FreshnessEvaluation(
            record_id=rec_id,
            status="possibly_stale",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            modified_paths=mod_overlap,
            reason=f"Linked paths modified between {observed_sha[:8]} and HEAD: {mod_overlap}",
        )

    # If net diff is zero, verify if intermediate commits touched the file (exact revert back to observed state)
    was_reverted = False
    try:
        res_log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline", f"{observed_sha}..{current_head}", "--"] + linked_paths,
            capture_output=True,
            text=True,
            check=False,
        )
        if res_log.returncode == 0 and res_log.stdout.strip():
            was_reverted = True
    except (subprocess.SubprocessError, OSError):
        pass

    if was_reverted:
        return FreshnessEvaluation(
            record_id=rec_id,
            status="fresh",
            comparison_status=diff_res.status,
            observed_commit=observed_sha,
            linked_paths=linked_paths,
            reason="Exact revert: content matches observed commit state despite intermediate history.",
        )

    return FreshnessEvaluation(
        record_id=rec_id,
        status="fresh",
        comparison_status=diff_res.status,
        observed_commit=observed_sha,
        linked_paths=linked_paths,
        reason="Clean worktree and no Git deltas since observed commit.",
    )


def check_record_freshness(
    repo: Path,
    record_path: Path,
    changed_files: Optional[set[str]] = None,
    current_head: str = "HEAD",
) -> str:
    """Assess whether a record's linked paths have changed across commits and working tree."""
    eval_res = evaluate_record_freshness(repo, record_path, changed_files, current_head)
    return eval_res.status


def update_repository_freshness(repo: Path) -> dict[str, str]:
    """Scan all technical and traceability records, updating freshness in place."""
    p = brain_paths(repo)
    with repo_lock(p):
        changed = set(get_git_changed_paths(repo))
        status_map = {}

        for folder_key in ("technical", "traceability"):
            folder = p[folder_key]
            if not folder.exists():
                continue
            for page in folder.glob("*.md"):
                freshness = check_record_freshness(repo, page, changed)
                status_map[page.name] = freshness
                # Update freshness in frontmatter if changed
                text = read_text(page)
                if "freshness:" in text:
                    new_text = re.sub(r"(?m)^freshness:\s*.*$", f"freshness: {freshness}", text)
                elif "---\n" in text:
                    new_text = text.replace("---\n", f"---\nfreshness: {freshness}\n", 1)
                elif "---\r\n" in text:
                    new_text = text.replace("---\r\n", f"---\r\nfreshness: {freshness}\r\n", 1)
                else:
                    new_text = text
                if new_text != text:
                    atomic_write(page, new_text)

        return status_map
