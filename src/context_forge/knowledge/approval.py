from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from context_forge.core.models import now_iso
from context_forge.core.budgets import Budgets
from context_forge.store.paths import read_text, read_json, atomic_write, brain_paths
from context_forge.store.registry import sync_routing_index, write_registry
from context_forge.knowledge.candidate import (
    candidate_file,
    sanitize_capture_delta,
    has_capture_content,
    compute_candidate_id,
)


def refresh_current_state(p: dict[str, Path], delta: dict[str, list[str]]) -> None:
    """Rewrite current-state.md from the most recent deltas only, hard-capped at L0_CHARS."""
    next_steps = delta.get("next_steps", [])
    decisions = delta.get("decisions", [])
    blockers = delta.get("blockers", [])

    body = ["# Current State", "", f"_Last updated {now_iso()} — auto-maintained, do not hand-edit._", ""]
    if decisions:
        body += ["## Recent decisions"] + [f"- {d}" for d in decisions] + [""]
    if blockers:
        body += ["## Open blockers / questions"] + [f"- {b}" for b in blockers] + [""]
    if next_steps:
        body += ["## Next steps"] + [f"- {n}" for n in next_steps] + [""]
    if not (decisions or blockers or next_steps):
        return

    text = "\n".join(body).strip() + "\n"
    if len(text) > Budgets.L0_CHARS:
        text = text[:Budgets.L0_CHARS].rsplit("\n", 1)[0] + "\n\n_(trimmed to budget; full history in log.md)_\n"
    atomic_write(p["current_state"], text)


def validate_pending_candidate(candidate: dict, candidate_id: str) -> Optional[str]:
    if not isinstance(candidate, dict) or candidate.get("status") != "pending":
        return "candidate is not pending"
    if candidate.get("id") != candidate_id:
        return "candidate id does not match its record"
    delta = candidate.get("delta")
    clean, redacted = sanitize_capture_delta(delta)
    if clean != delta:
        return "candidate contains malformed or unsanitized content"
    if redacted or candidate.get("contains_redactions"):
        return "candidate contains redacted secret material and requires a human-authored sanitized record"
    if not has_capture_content(delta):
        return "candidate contains no promotable facts"
    expected = compute_candidate_id(candidate.get("session", ""), delta)
    if expected != candidate_id:
        return "candidate integrity check failed"
    return None


def approved_log_entry(candidate: dict) -> str:
    delta = candidate["delta"]
    candidate_id = candidate["id"]
    source = candidate.get("source", "deterministic")
    lines = [f"\n## [{now_iso()}] review-approved candidate:{candidate_id} | {source}"]
    for key in ("decisions", "blockers", "next_steps"):
        for item in delta.get(key, []):
            lines.append(f"- ({key}) {item}")
    return "\n".join(lines) + "\n"


def approve_candidate(repo: Path, candidate_id: str, identity: IdentityEnvelope | None = None) -> int:
    """Promote exactly one reviewed candidate from .brain/.state/pending into canonical memory with ApprovalReceipt."""
    from context_forge.store.lock import repo_lock
    from context_forge.core.identity import build_identity_envelope, get_git_info
    from context_forge.core.models import ApprovalReceipt, IdentityEnvelope
    from context_forge.store.audit import append_audit_log

    p = brain_paths(repo)
    with repo_lock(p):
        try:
            pending_path = candidate_file(p["pending"], candidate_id)
            approved_path = candidate_file(p["approved"], candidate_id)
        except ValueError as exc:
            print(f"[brain] approval rejected: {exc}")
            return 1

        if not pending_path.exists():
            if approved_path.exists():
                print(f"[brain] candidate {candidate_id} was already approved; no duplicate promotion occurred")
            else:
                print(f"[brain] pending candidate {candidate_id} was not found")
            return 1

        candidate = read_json(pending_path, {})
        problem = validate_pending_candidate(candidate, candidate_id)
        if problem:
            print(f"[brain] approval rejected: {problem}")
            return 1

        actual_identity = identity or build_identity_envelope(repo=repo)
        commit_sha, _, _ = get_git_info(repo) if (repo / ".git").exists() else ("", "", "")
        if commit_sha and not actual_identity.commit_sha:
            actual_identity.commit_sha = commit_sha

        receipt = ApprovalReceipt(
            candidate_id=candidate_id,
            approved_at=now_iso(),
            reviewer_identity=actual_identity,
            candidate_digest=candidate_id,
            commit_sha=actual_identity.commit_sha,
            promotion_target="log.md",
            approval_channel="cli_review",
        )

        marker = f"candidate:{candidate_id}"
        log_text = read_text(p["log"])
        if marker not in log_text:
            atomic_write(p["log"], log_text + approved_log_entry(candidate))
        refresh_current_state(p, candidate["delta"])

        sync_routing_index(p)
        write_registry(repo, p)

        candidate["status"] = "approved"
        candidate["approved_at"] = now_iso()
        candidate["approval_receipt"] = receipt.to_dict()

        p["approved"].mkdir(parents=True, exist_ok=True)
        atomic_write(approved_path, json.dumps(candidate, indent=2, sort_keys=True) + "\n")
        pending_path.unlink(missing_ok=True)

        append_audit_log(
            p,
            f"approve candidate:{candidate_id}",
            approved_path,
            f"Candidate {candidate_id} promoted to canonical log.md with approval receipt.",
            identity=actual_identity,
        )

        print(f"[brain] approved candidate {candidate_id}; canonical hot memory and index refreshed")
        return 0
