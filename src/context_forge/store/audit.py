from __future__ import annotations

from pathlib import Path
from context_forge.core.models import now_iso, today, IdentityEnvelope
from context_forge.store.paths import read_text, atomic_write


def append_audit_log(
    p: dict[str, Path],
    action: str,
    record: Path,
    detail: str,
    identity: IdentityEnvelope | None = None,
) -> None:
    """Record an auditable, append-only knowledge event with actor provenance."""
    p["audit"].mkdir(parents=True, exist_ok=True)
    path = p["audit"] / f"knowledge-{today()}.md"
    if not path.exists():
        atomic_write(path, f"# Knowledge Audit — {today()}\n")

    agent_str = f" · Agent: `{identity.agent_id}` ({identity.agent_role})" if identity and identity.agent_id else ""
    commit_str = f" · Commit: `{identity.commit_sha}`" if identity and identity.commit_sha else ""
    record_rel = record.relative_to(p["root"]).as_posix() if p["root"] in record.parents else str(record)

    entry = (
        f"\n## {now_iso()} — {action}{agent_str}{commit_str}\n\n"
        f"- Record: `{record_rel}`\n"
        f"- {detail}\n"
    )
    atomic_write(path, read_text(path).rstrip() + entry)
