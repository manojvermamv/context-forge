from __future__ import annotations

import re
from pathlib import Path
from context_forge.core.models import KnowledgeRecord, EvidenceStatement, IdentityEnvelope, today
from context_forge.core.authority import EpistemicAuthority
from context_forge.core.evidence import sanitize_evidence
from context_forge.store.paths import brain_paths, atomic_write, read_text
from context_forge.store.audit import append_audit_log
from context_forge.store.registry import sync_routing_index, write_registry


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or "record"


def next_record_id(p: dict[str, Path], prefix: str) -> str:
    highest = 0
    for page in p["root"].rglob("*.md"):
        match = re.search(rf"(?m)^id:\s*{re.escape(prefix)}-(\d+)\s*$", read_text(page))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def write_traceability(p: dict[str, Path], record_id: str, title: str, scope: list[str]) -> Path | None:
    if not scope:
        return None
    trace_id = next_record_id(p, "TRACE")
    destination = p["traceability"] / f"{trace_id}-{slugify(title)}.md"
    paths = "\n".join(f"- `{item.replace(chr(92), '/')}`" for item in scope)
    atomic_write(
        destination,
        "---\n"
        f"id: {trace_id}\nstatus: linked\nauthority: derived_link\nupdated: {today()}\n---\n\n"
        f"# Traceability — {title}\n\n## Source record\n\n- `{record_id}`\n\n"
        f"## Related paths\n\n{paths}\n"
    )
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
    """Validate authority, create a durable KnowledgeRecord, audit, and link traceability."""
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
    }
    prefix, section, status = policy[kind]
    record_id = next_record_id(p, prefix)
    path = p[section] / f"{record_id}-{slugify(title)}.md"
    normalized_scope = [item.replace("\\", "/") for item in scope]

    record = KnowledgeRecord(
        id=record_id,
        kind=kind,
        title=title,
        status=status,
        authority=authority,
        updated=today(),
        body=body,
        evidence=EvidenceStatement(statement=evidence, source_type="user_input" if authority == "user_explicit" else "code_observed"),
        identity=identity or IdentityEnvelope(),
        scope=normalized_scope,
        path=path.relative_to(p["root"]).as_posix(),
    )

    atomic_write(path, record.to_markdown())
    append_audit_log(p, f"record {kind}", path, f"Authority: `{authority}`; status: `{status}`.", identity)

    trace = write_traceability(p, record_id, title, normalized_scope) if kind in ("decision", "requirement") else None
    if trace:
        append_audit_log(p, "traceability", trace, f"Linked `{record_id}` to {len(normalized_scope)} path(s).", identity)

    sync_routing_index(p)
    write_registry(repo, p)

    print(f"[brain] recorded {record_id} at {path.relative_to(repo)}")
    return 0
