from __future__ import annotations

import re
from pathlib import Path
from context_forge.core.models import KnowledgeRecord, EvidenceStatement, IdentityEnvelope, today
from context_forge.core.authority import EpistemicAuthority
from context_forge.core.evidence import sanitize_evidence
from context_forge.core.identity import build_identity_envelope, get_git_info
from context_forge.store.paths import brain_paths, atomic_write, read_text
from context_forge.store.audit import append_audit_log
from context_forge.store.registry import sync_routing_index, write_registry
from context_forge.store.lock import repo_lock


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or "record"


def next_record_id(p: dict[str, Path], prefix: str) -> str:
    """Find the next monotonically increasing ID for a record prefix."""
    highest = 0
    for page in p["root"].rglob("*.md"):
        match = re.search(rf"(?m)^id:\s*{re.escape(prefix)}-(\d+)\s*$", read_text(page))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def write_traceability(p: dict[str, Path], record_id: str, title: str, scope: list[str], commit_sha: str = "") -> Path | None:
    if not scope:
        return None
    with repo_lock(p):
        trace_id = next_record_id(p, "TRACE")
        p["traceability"].mkdir(parents=True, exist_ok=True)
        destination = p["traceability"] / f"{trace_id}-{slugify(title)}.md"
        paths = "\n".join(f"- `{item.replace(chr(92), '/')}`" for item in scope)
        fm_lines = [
            "---",
            f"id: {trace_id}",
            "status: linked",
            "authority: derived_link",
            f"updated: {today()}",
        ]
        if commit_sha:
            fm_lines.append(f"commit_sha: {commit_sha}")
            fm_lines.append(f"evidence_observed_commit: {commit_sha}")
        fm_lines.extend([
            "---",
            "",
            f"# Traceability — {title}",
            "",
            "## Source record",
            "",
            f"- `{record_id}`",
            "",
            "## Related paths",
            "",
            paths,
            "",
        ])
        atomic_write(destination, "\n".join(fm_lines))
        return destination


def create_knowledge_record(
    repo: Path,
    kind: str,
    title: str,
    body: str,
    authority: str,
    evidence: str,
    scope: list[str],
    accept: bool,
    identity: IdentityEnvelope | None = None,
) -> int:
    """Validate authority, allocate ID, create durable KnowledgeRecord, audit, and link traceability under lock."""
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1

    try:
        title = sanitize_evidence(title, "title")
        body = sanitize_evidence(body, "body")
        evidence = sanitize_evidence(evidence, "evidence") if evidence else "Not supplied."
    except ValueError as exc:
        print(f"[brain] update rejected: {exc}")
        return 1

    valid, reason = EpistemicAuthority.can_accept_intent(kind, authority, accept)
    if not valid:
        print(f"[brain] update rejected: {reason}")
        return 1

    policy = {
        "decision": ("ADR", "decisions", "accepted"),
        "requirement": ("REQ", "requirements", "accepted"),
        "technical": ("TECH", "technical", "observed"),
        "question": ("Q", "questions", "open"),
        "policy": ("POL", "policies", "accepted"),
        "invariant": ("INV", "policies", "accepted"),
    }
    if kind not in policy:
        print(f"[brain] update rejected: unknown kind '{kind}'")
        return 1

    with repo_lock(p):
        prefix, section, status = policy[kind]
        record_id = next_record_id(p, prefix)
        p[section].mkdir(parents=True, exist_ok=True)
        path = p[section] / f"{record_id}-{slugify(title)}.md"
        normalized_scope = [item.replace("\\", "/") for item in scope]

        actual_identity = identity or build_identity_envelope(repo=repo)
        if (repo / ".git").exists() and not actual_identity.commit_sha:
            c_sha, b_name, w_tree = get_git_info(repo)
            actual_identity.commit_sha = c_sha
            if not actual_identity.branch:
                actual_identity.branch = b_name
            if not actual_identity.worktree:
                actual_identity.worktree = w_tree

        record = KnowledgeRecord(
            id=record_id,
            kind=kind,
            title=title,
            status=status,
            authority=authority,
            updated=today(),
            body=body,
            evidence=EvidenceStatement(
                statement=evidence,
                source_type="user_input" if authority == "user_explicit" else ("policy_mandate" if authority == "policy_mandate" else "code_observed"),
                observed_commit=actual_identity.commit_sha or "",
            ),
            identity=actual_identity,
            scope=normalized_scope,
            path=path.relative_to(p["root"]).as_posix(),
        )

        atomic_write(path, record.to_markdown())
        append_audit_log(p, f"record {kind}", path, f"Authority: `{authority}`; status: `{status}`.", actual_identity)

        trace = write_traceability(p, record_id, title, normalized_scope, actual_identity.commit_sha) if kind in ("decision", "requirement", "policy", "invariant") else None
        if trace:
            append_audit_log(p, "traceability", trace, f"Linked `{record_id}` to {len(normalized_scope)} path(s).", actual_identity)

        sync_routing_index(p)
        write_registry(repo, p)
        if p["search_index_db"].exists() or p["search_index_json"].exists():
            from context_forge.cli.commands import cmd_index
            cmd_index(repo)

        print(f"[brain] recorded {record_id} at {path.relative_to(repo)}")
        return 0
