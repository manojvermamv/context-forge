from __future__ import annotations

import json
from pathlib import Path
from context_forge.core.models import today, now_iso
from context_forge.store.paths import brain_paths, atomic_write
from context_forge.store.audit import append_audit_log
from context_forge.store.registry import sync_routing_index, write_registry
from context_forge.knowledge.update import next_record_id
from context_forge.traceability.freshness import get_git_changed_paths, update_repository_freshness


def reconcile_sync(repo: Path, explicit_paths: list[str], apply: bool) -> int:
    """Reconcile manual or external source changes as code-observed evidence."""
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1

    changed = sorted(set(explicit_paths or get_git_changed_paths(repo)))
    if not changed:
        print("[brain] sync found no changed paths; pass --path for files changed outside Git.")
        return 0

    p["reports"].mkdir(parents=True, exist_ok=True)
    report = p["reports"] / f"sync-{today()}.json"
    atomic_write(report, json.dumps({"generated_at": now_iso(), "paths": changed}, indent=2) + "\n")

    if not apply:
        print(
            f"[brain] sync plan: {len(changed)} changed paths; inspect {report.relative_to(repo)} "
            "then rerun with --apply"
        )
        return 0

    record_id = next_record_id(p, "TECH")
    destination = p["technical"] / f"{record_id}-observed-changes-{today()}.md"
    lines = "\n".join(f"- `{item}`" for item in changed)
    atomic_write(
        destination,
        "---\n"
        f"id: {record_id}\nstatus: observed\nauthority: code_observed\nupdated: {today()}\n---\n\n"
        f"# Observed Changes — {today()}\n\n"
        "These paths changed outside the current agent workflow. This record documents observed "
        "state only; it does not assert product intent or approve a decision.\n\n"
        f"## Related paths\n\n{lines}\n\n## Evidence\n\n- `{report.relative_to(repo).as_posix()}`\n"
    )

    append_audit_log(p, "sync", destination, f"Recorded {len(changed)} externally changed paths as technical evidence.")
    update_repository_freshness(repo)
    sync_routing_index(p)
    write_registry(repo, p)

    print(f"[brain] sync applied — recorded {len(changed)} changed paths as code-observed evidence")
    return 0
