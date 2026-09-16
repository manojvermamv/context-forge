from __future__ import annotations

import re
from pathlib import Path
from context_forge.store.paths import brain_paths, read_text, atomic_write
from context_forge.store.lock import repo_lock


def find_record_file(root: Path, record_id: str) -> Path | None:
    """Find the Markdown file path matching a record ID under .brain/."""
    for page in root.rglob("*.md"):
        text = read_text(page)
        if re.search(rf"(?m)^id:\s*{re.escape(record_id)}\s*$", text):
            return page
    return None


def get_superseded_by(root: Path, record_id: str) -> str:
    """Retrieve superseded_by target ID for a given record ID if present."""
    f = find_record_file(root, record_id)
    if not f:
        return ""
    m = re.search(r"(?m)^superseded_by:\s*([A-Za-z0-9_-]+)", read_text(f))
    return m.group(1).strip() if m else ""


def supersede_record(repo: Path, old_id: str, new_id: str) -> bool:
    """Atomically establish bidirectional supersession between old_id and new_id under lock.
    
    Old record: status: superseded, superseded_by: new_id
    New record: supersedes: old_id
    
    Invariants:
    1. Rejects self-supersession (old_id == new_id).
    2. Rejects missing source or target records.
    3. Rejects cycle attempts (e.g. ADR-003 -> ADR-001 when ADR-001 -> ADR-002 -> ADR-003 exists).
    4. Idempotent execution.
    """
    clean_old = (old_id or "").strip()
    clean_new = (new_id or "").strip()
    if not clean_old or not clean_new or clean_old == clean_new:
        return False

    p = brain_paths(repo)
    if not p["root"].exists():
        return False

    with repo_lock(p):
        old_file = find_record_file(p["root"], clean_old)
        new_file = find_record_file(p["root"], clean_new)

        if not old_file or not new_file:
            return False

        # Cycle detection: walk chain from clean_new -> successor -> ...
        curr = clean_new
        visited = set()
        while curr:
            if curr == clean_old:
                # Cycle detected!
                return False
            if curr in visited:
                break
            visited.add(curr)
            curr = get_superseded_by(p["root"], curr)

        # Update Old Record: status: superseded, superseded_by: clean_new
        old_text = read_text(old_file)
        old_updated = re.sub(r"(?m)^status:\s*.*$", "status: superseded", old_text)
        if "superseded_by:" in old_updated:
            old_updated = re.sub(r"(?m)^superseded_by:\s*.*$", f"superseded_by: {clean_new}", old_updated)
        elif "updated:" in old_updated:
            old_updated = re.sub(r"(?m)^updated:\s*(.*)$", f"superseded_by: {clean_new}\nupdated: \\1", old_updated)
        else:
            old_updated = old_updated.replace("---\n", f"---\nsuperseded_by: {clean_new}\n", 1)

        if old_updated != old_text:
            atomic_write(old_file, old_updated)

        # Update New Record: supersedes: clean_old
        new_text = read_text(new_file)
        new_updated = new_text
        if "supersedes:" in new_updated:
            new_updated = re.sub(r"(?m)^supersedes:\s*.*$", f"supersedes: {clean_old}", new_updated)
        elif "updated:" in new_updated:
            new_updated = re.sub(r"(?m)^updated:\s*(.*)$", f"supersedes: {clean_old}\nupdated: \\1", new_updated)
        else:
            new_updated = new_updated.replace("---\n", f"---\nsupersedes: {clean_old}\n", 1)

        if new_updated != new_text:
            atomic_write(new_file, new_updated)

        return True
