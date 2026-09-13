from __future__ import annotations

import re
from pathlib import Path
from context_forge.store.paths import read_text, atomic_write


def supersede_record(repo: Path, old_id: str, new_id: str) -> bool:
    """Mark an existing record as superseded by a newer record."""
    root = repo / ".brain"
    for page in root.rglob("*.md"):
        text = read_text(page)
        if re.search(rf"(?m)^id:\s*{re.escape(old_id)}\s*$", text):
            # Update status and add superseded_by
            text = re.sub(r"(?m)^status:\s*.*$", "status: superseded", text)
            if "superseded_by:" not in text:
                text = re.sub(r"(?m)^updated:\s*(.*)$", f"superseded_by: {new_id}\nupdated: \\1", text)
            else:
                text = re.sub(r"(?m)^superseded_by:\s*.*$", f"superseded_by: {new_id}", text)
            atomic_write(page, text)
            return True
    return False
