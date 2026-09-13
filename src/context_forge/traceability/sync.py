from __future__ import annotations

import json
from pathlib import Path
from context_forge.core.models import today, now_iso, IdentityEnvelope
from context_forge.core.identity import get_git_info
from context_forge.store.paths import brain_paths, atomic_write
from context_forge.store.audit import append_audit_log
from context_forge.store.registry import sync_routing_index, write_registry
from context_forge.knowledge.update import next_record_id
from context_forge.traceability.freshness import get_git_changed_paths, update_repository_freshness


def reconcile_sync(repo: Path, explicit_paths: list[str], apply: bool) -> int:
    """Reconcile manual or external source changes as commit-anchored code-observed evidence."""
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

    commit_sha, branch, worktree = ("", "", "")
    dirty = False
    if (repo / ".git").exists():
        commit_sha, branch, worktree = get_git_info(repo)
        dirty_files = get_git_changed_paths(repo)
        dirty = bool(dirty_files)

    from context_forge.store.lock import repo_lock
    with repo_lock(p):
        record_id = next_record_id(p, "TECH")
        destination = p["technical"] / f"{record_id}-observed-changes-{today()}.md"
        lines = "\n".join(f"- `{item}`" for item in changed)

        fm_lines = [
            "---",
            f"id: {record_id}",
            "kind: technical",
            "status: observed",
            "authority: code_observed",
            f"updated: {today()}",
            f"created_at: {now_iso()}",
            "schema_version: 2.0",
            f"project_id: {repo.name}",
            f"repository: {repo.name}",
        ]
        if commit_sha:
            fm_lines.append(f"commit_sha: {commit_sha}")
            fm_lines.append(f"evidence_observed_commit: {commit_sha}")
        if branch:
            fm_lines.append(f"branch: {branch}")
        if worktree:
            fm_lines.append(f"worktree: {worktree}")
        if dirty:
            fm_lines.append("dirty: true")

        fm_lines.extend([
            "producer: scanner",
            "producer_type: tool",
            "authority_domain: IMPLEMENTATION",
            "authority_level: 70",
            "evidence_source_type: code_observed",
            f"evidence_verification_state: {'unverified' if dirty else 'verified'}",
            f"freshness: {'possibly_stale' if dirty else 'fresh'}",
            "---",
            "",
            f"# Observed Changes — {today()}",
            "",
            "These paths changed outside the current agent workflow. This record documents observed "
            "state only; it does not assert product intent or approve a decision.",
            "",
            "## Related paths",
            "",
            lines,
            "",
            "## Evidence",
            "",
            f"- `{report.relative_to(repo).as_posix()}`",
            "",
        ])

        atomic_write(destination, "\n".join(fm_lines))

        audit_ident = IdentityEnvelope(
            commit_sha=commit_sha,
            branch=branch,
            producer="scanner",
            producer_type="tool",
        )
        append_audit_log(
            p,
            "sync",
            destination,
            f"Recorded {len(changed)} externally changed paths as commit-anchored technical evidence.",
            identity=audit_ident,
        )
        update_repository_freshness(repo)
        sync_routing_index(p)
        write_registry(repo, p)

    print(f"[brain] sync applied — recorded {len(changed)} changed paths as code-observed evidence")
    return 0
