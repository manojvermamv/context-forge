from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from context_forge.core.budgets import Budgets
from context_forge.core.models import today, now_iso
from context_forge.store.paths import brain_paths, read_text, atomic_write
from context_forge.store.registry import sync_routing_index, write_registry


def plan_or_apply_consolidation(repo: Path, apply: bool = False) -> int:
    """Rotate aged log entries (> LOG_ROTATE_DAYS) to a durable concept summary."""
    p = brain_paths(repo)
    log_text = read_text(p["log"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=Budgets.LOG_ROTATE_DAYS)
    aged, kept = [], []

    for block in re.split(r"(?=\n## \[)", log_text):
        m = re.match(r"\n?## \[([\dT:\-Z]+)\]", block)
        if not m:
            kept.append(block)
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            kept.append(block)
            continue
        (aged if ts < cutoff else kept).append(block)

    if not aged:
        print("[brain] nothing older than the rotation window — nothing to consolidate")
        return 0

    target_concept = p["concepts"] / f"log-summary-{today()}.md"
    if not apply:
        print(
            f"[brain] consolidation plan: {len(aged)} aged log entries would move to "
            f"{target_concept.relative_to(repo)}. "
            "Review it, then rerun with --apply to make this canonical-memory write."
        )
        return 0

    header = (
        f"# Log summary ({len(aged)} entries older than {Budgets.LOG_ROTATE_DAYS}d, "
        f"consolidated {now_iso()})\n\n_Raw entries removed from log.md after this point; "
        f"this page is the durable record._\n\n"
    )
    atomic_write(target_concept, header + "".join(aged))
    atomic_write(p["log"], "".join(kept) or "# log.md\n\n_(rotated — see concepts/log-summary-*.md for history)_\n")
    sync_routing_index(p)
    write_registry(repo, p)

    print(f"[brain] consolidated {len(aged)} aged entries into {target_concept.relative_to(repo)}")
    return 0
