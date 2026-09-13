from __future__ import annotations

import re
from pathlib import Path
from context_forge.core.models import KnowledgeRecord, EvidenceStatement, IdentityEnvelope, today


def extract_frontmatter_dict(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-style frontmatter between --- markers without PyYAML."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text

    fm_raw = text[4:end]
    body = text[end + 4:].lstrip("\r\n")

    res = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            res[key.strip()] = val.strip().strip('"\'')
    return res, body


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_markdown_record(path: Path, root: Path) -> KnowledgeRecord:
    """Read a Markdown file from .brain/ and convert into a KnowledgeRecord."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = extract_frontmatter_dict(text)
    rel_path = path.relative_to(root).as_posix()

    first_part = path.relative_to(root).parts[0] if path != root else ""
    kind = {
        "decisions": "decision",
        "requirements": "requirement",
        "technical": "technical",
        "traceability": "traceability",
        "questions": "question",
        "concepts": "concept",
    }.get(first_part, "core")

    title = first_heading(text) or path.stem

    evidence_text = "Not supplied."
    if "## Evidence" in body:
        parts = body.split("## Evidence", 1)[1]
        if "##" in parts:
            evidence_text = parts.split("##", 1)[0].strip()
        else:
            evidence_text = parts.strip()

    scope = []
    if "## Related paths" in body:
        paths_part = body.split("## Related paths", 1)[1]
        for line in paths_part.splitlines():
            line = line.strip()
            if line.startswith("- `") and line.endswith("`"):
                scope.append(line[3:-1])

    return KnowledgeRecord(
        id=fm.get("id", path.stem),
        kind=kind,
        title=title,
        status=fm.get("status", "accepted"),
        authority=fm.get("authority", "code_observed"),
        updated=fm.get("updated") or fm.get("date") or today(),
        body=body,
        evidence=EvidenceStatement(statement=evidence_text),
        identity=IdentityEnvelope(
            agent_id=fm.get("agent_id", ""),
            commit_sha=fm.get("commit_sha", ""),
        ),
        supersedes=fm.get("supersedes", ""),
        superseded_by=fm.get("superseded_by", ""),
        freshness=fm.get("freshness", "fresh"),
        path=rel_path,
    )


def serialize_markdown_record(record: KnowledgeRecord) -> str:
    return record.to_markdown()
