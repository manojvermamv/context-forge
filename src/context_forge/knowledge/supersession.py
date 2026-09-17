from __future__ import annotations

import json
import re
from pathlib import Path
from context_forge.core.models import now_iso
from context_forge.store.paths import brain_paths, read_text, read_json, atomic_write
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


def recover_pending_journal(p: dict[str, Path]) -> None:
    """Recover uncommitted multi-file atomic writes if a process crashed mid-operation."""
    journal_file = p["state"] / "journal.json"
    if not journal_file.exists():
        return
    data = read_json(journal_file, {})
    if isinstance(data, dict) and data.get("status") == "pending_writes":
        writes = data.get("writes", [])
        for item in writes:
            if isinstance(item, dict) and "path" in item and "content" in item:
                target = Path(item["path"])
                atomic_write(target, item["content"])
        journal_file.unlink(missing_ok=True)


def record_journal_writes(p: dict[str, Path], writes: list[dict[str, str]]) -> Path:
    """Record pending multi-file atomic writes to journal before applying."""
    p["state"].mkdir(parents=True, exist_ok=True)
    journal_file = p["state"] / "journal.json"
    payload = {
        "status": "pending_writes",
        "created_at": now_iso(),
        "writes": writes,
    }
    atomic_write(journal_file, json.dumps(payload, indent=2) + "\n")
    return journal_file


def supersede_record(repo: Path, old_id: str, new_id: str) -> bool:
    """Atomically establish bidirectional supersession between old_id and new_id under lock.
    
    Old record: status: superseded, superseded_by: new_id
    New record: supersedes: old_id
    
    Invariants:
    1. Rejects self-supersession (old_id == new_id).
    2. Rejects missing source or target records.
    3. Rejects cycle attempts (e.g. ADR-003 -> ADR-001 when ADR-001 -> ADR-002 -> ADR-003 exists).
    4. Multi-file crash recovery via mutation journal (.brain/.state/journal.json).
    5. Idempotent execution.
    """
    clean_old = (old_id or "").strip()
    clean_new = (new_id or "").strip()
    if not clean_old or not clean_new or clean_old == clean_new:
        return False

    p = brain_paths(repo)
    if not p["root"].exists():
        return False

    with repo_lock(p):
        recover_pending_journal(p)

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

        # Prepare Old Record update: status: superseded, superseded_by: clean_new
        old_text = read_text(old_file)
        old_updated = re.sub(r"(?m)^status:\s*.*$", "status: superseded", old_text)
        if "superseded_by:" in old_updated:
            old_updated = re.sub(r"(?m)^superseded_by:\s*.*$", f"superseded_by: {clean_new}", old_updated)
        elif "updated:" in old_updated:
            old_updated = re.sub(r"(?m)^updated:\s*(.*)$", f"superseded_by: {clean_new}\nupdated: \\1", old_updated)
        else:
            old_updated = old_updated.replace("---\n", f"---\nsuperseded_by: {clean_new}\n", 1)

        # Prepare New Record update: supersedes: clean_old
        new_text = read_text(new_file)
        new_updated = new_text
        if "supersedes:" in new_updated:
            new_updated = re.sub(r"(?m)^supersedes:\s*.*$", f"supersedes: {clean_old}", new_updated)
        elif "updated:" in new_updated:
            new_updated = re.sub(r"(?m)^updated:\s*(.*)$", f"supersedes: {clean_old}\nupdated: \\1", new_updated)
        else:
            new_updated = new_updated.replace("---\n", f"---\nsupersedes: {clean_old}\n", 1)

        writes = []
        if old_updated != old_text:
            writes.append({"path": str(old_file), "content": old_updated})
        if new_updated != new_text:
            writes.append({"path": str(new_file), "content": new_updated})

        if writes:
            journal_file = record_journal_writes(p, writes)
            for w in writes:
                atomic_write(Path(w["path"]), w["content"])
            journal_file.unlink(missing_ok=True)

        return True
