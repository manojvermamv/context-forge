from __future__ import annotations

import re
import subprocess
from pathlib import Path
from context_forge.store.paths import brain_paths, read_text, atomic_write


def get_git_changed_paths(repo: Path) -> list[str]:
    """Return paths changed according to git status --porcelain."""
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


def check_record_freshness(repo: Path, record_path: Path, changed_files: set[str]) -> str:
    """Assess whether a technical or traceability record's linked paths have changed."""
    text = read_text(record_path)
    # Find all path references
    linked_paths = set(re.findall(r"- `([^`]+\.[a-zA-Z0-9]+)`", text))
    if not linked_paths:
        return "fresh"

    # If any linked path is in git changed files
    overlap = linked_paths.intersection(changed_files)
    if overlap:
        return "possibly_stale"

    # Verify if paths still exist on disk
    for lp in linked_paths:
        if not (repo / lp).exists():
            return "stale"

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
