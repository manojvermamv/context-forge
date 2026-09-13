from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional
from context_forge.store.paths import brain_paths, read_text, atomic_write


def get_git_changed_paths(repo: Path) -> list[str]:
    """Return uncommitted modified, untracked, or deleted paths in working tree."""
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
            return []
        paths = []
        for line in result.stdout.splitlines():
            if len(line) >= 4:
                paths.append(line[3:].split(" -> ")[-1].replace("\\", "/"))
        return sorted(set(paths))
    except Exception:
        return []


def get_git_diff_paths(
    repo: Path,
    base_sha: str,
    target_sha: str = "HEAD",
) -> tuple[set[str], set[str], set[str]]:
    """Return (modified, deleted, renamed) paths between base_sha and target_sha."""
    if not base_sha or not (repo / ".git").exists():
        return set(), set(), set()
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-status", f"{base_sha}..{target_sha}"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if res.returncode != 0:
            return set(), set(), set()
        modified, deleted, renamed = set(), set(), set()
        for line in res.stdout.splitlines():
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
    except Exception:
        return set(), set(), set()


def extract_record_commit(record_text: str) -> str:
    """Extract observed commit SHA from frontmatter."""
    m = re.search(r"(?m)^commit_sha:\s*([a-fA-F0-9]+)", record_text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?m)^observed_commit:\s*([a-fA-F0-9]+)", record_text)
    if m2:
        return m2.group(1).strip()
    return ""


def check_record_freshness(
    repo: Path,
    record_path: Path,
    changed_files: Optional[set[str]] = None,
    current_head: str = "HEAD",
) -> str:
    """Assess whether a technical or traceability record's linked paths have changed across commits and working tree."""
    text = read_text(record_path)
    # Find all path references
    linked_paths = set(re.findall(r"- `([^`]+\.[a-zA-Z0-9]+)`", text))
    if not linked_paths:
        return "fresh"

    # 1. Verify if paths still exist on disk
    for lp in linked_paths:
        if not (repo / lp).exists():
            return "stale"

    # 2. Check uncommitted working tree dirty files
    dirty = set(get_git_changed_paths(repo)) if changed_files is None else set(changed_files)
    if linked_paths.intersection(dirty):
        return "possibly_stale"

    # 3. Living Git Freshness: Commit-anchored diff between observed_commit_sha and HEAD
    observed_sha = extract_record_commit(text)
    if observed_sha:
        mod_diff, del_diff, ren_diff = get_git_diff_paths(repo, observed_sha, current_head)
        if linked_paths.intersection(del_diff) or linked_paths.intersection(ren_diff):
            return "stale"
        if linked_paths.intersection(mod_diff):
            return "possibly_stale"

    return "fresh"


def update_repository_freshness(repo: Path) -> dict[str, str]:
    """Scan all technical and traceability records, updating freshness in place."""
    p = brain_paths(repo)
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
            else:
                new_text = text
            if new_text != text:
                atomic_write(page, new_text)

    return status_map

