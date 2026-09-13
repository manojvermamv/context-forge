from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from context_forge.core.models import KnowledgeRecord, EvidenceStatement, IdentityEnvelope, today


def extract_frontmatter_dict(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-style frontmatter between --- markers without PyYAML."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text

    end = -1
    for marker in ("\n---\n", "\r\n---\r\n", "\n---\r\n", "\r\n---\n"):
        idx = text.find(marker, 3)
        if idx >= 0 and (end == -1 or idx < end):
            end = idx
            break

    if end < 0:
        # Check if --- is followed by newline or EOF
        idx = text.find("\n---", 3)
        if idx >= 0:
            end = idx
        else:
            return {}, text

    fm_raw = text[4:end]
    # Find where body starts (after closing --- line)
    after_end = text.find("\n", end + 4)
    body = text[after_end + 1:].lstrip("\r\n") if after_end >= 0 else ""

    res: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in fm_raw.splitlines():
        line = raw_line.rstrip()
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        if (line.startswith("  - ") or line.startswith("    - ") or line.startswith("- ")) and current_key:
            val = line.split("-", 1)[1].strip().strip('"\'')
            if not isinstance(res.get(current_key), list):
                res[current_key] = []
            res[current_key].append(val)
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"\'')
            current_key = key
            if val == "":
                res[key] = []
            elif val.startswith("[") and val.endswith("]"):
                try:
                    res[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    res[key] = [x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
            else:
                res[key] = val
        else:
            current_key = None

    return res, body


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_markdown_record(path: Path, root: Path) -> KnowledgeRecord:
    """Read a Markdown file from .brain/ and convert into a KnowledgeRecord with full provenance."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = extract_frontmatter_dict(text)
    try:
        rel_path = path.relative_to(root).as_posix()
        first_part = path.relative_to(root).parts[0] if path != root else ""
    except ValueError:
        rel_path = path.name
        first_part = ""

    # Infer record kind
    kind = fm.get("kind")
    if not kind:
        kind_map = {
            "decisions": "decision",
            "requirements": "requirement",
            "technical": "technical",
            "traceability": "traceability",
            "questions": "question",
            "concepts": "concept",
            "policies": "policy",
            "invariants": "invariant",
        }
        kind = kind_map.get(first_part)
        if not kind:
            rec_id = fm.get("id", path.stem).upper()
            if rec_id.startswith("ADR-"):
                kind = "decision"
            elif rec_id.startswith("REQ-"):
                kind = "requirement"
            elif rec_id.startswith("TECH-"):
                kind = "technical"
            elif rec_id.startswith("Q-"):
                kind = "question"
            elif rec_id.startswith("TRACE-"):
                kind = "traceability"
            elif rec_id.startswith("POL-"):
                kind = "policy"
            elif rec_id.startswith("INV-"):
                kind = "invariant"
            elif rec_id.startswith("CONCEPT-"):
                kind = "concept"
            else:
                kind = "technical"

    title = first_heading(text) or path.stem

    evidence_text = "Not supplied."
    if "## Evidence" in body:
        parts = body.split("## Evidence", 1)[1]
        if "\n## " in parts:
            evidence_text = parts.split("\n## ", 1)[0].strip()
        else:
            evidence_text = parts.strip()

    # Extract clean section content if standard headings are present
    content_body = body
    heading_pattern = r"(?m)^## (Decision|Requirement|Observed behavior|Question|Summary|Source record|Policy|Invariant|Content)\s*$"
    match = re.search(heading_pattern, body)
    if match:
        after_heading = body[match.end():].lstrip("\r\n")
        next_heading = re.search(r"(?m)^## ", after_heading)
        if next_heading:
            extracted = after_heading[:next_heading.start()].strip()
        else:
            extracted = after_heading.strip()
        content_body = "" if extracted == "_(none)_" else extracted

    scope = []
    if fm.get("scope"):
        raw_sc = fm["scope"]
        scope = [str(x) for x in raw_sc] if isinstance(raw_sc, list) else [str(raw_sc)]
    elif "## Related paths" in body:
        paths_part = body.split("## Related paths", 1)[1]
        for line in paths_part.splitlines():
            line = line.strip()
            if line.startswith("- `") and line.endswith("`"):
                scope.append(line[3:-1])

    # Parse Evidence metadata
    ev_refs = fm.get("evidence_source_refs")
    if isinstance(ev_refs, str):
        ev_refs = [ev_refs]
    elif not isinstance(ev_refs, list):
        ev_refs = []
        if fm.get("source_ref"):
            ev_refs = [str(fm.get("source_ref"))]

    raw_redact = fm.get("evidence_contains_redactions")
    if raw_redact is None:
        raw_redact = fm.get("contains_redactions", False)
    contains_redactions = str(raw_redact).lower() in ("true", "1", "yes")

    ev_auth = 0
    if "evidence_authority_level" in fm:
        try:
            ev_auth = int(fm["evidence_authority_level"])
        except (ValueError, TypeError):
            ev_auth = 0

    evidence = EvidenceStatement(
        statement=evidence_text,
        authority_level=ev_auth,
        source_type=str(fm.get("evidence_source_type") or fm.get("source_type") or "code_observed"),
        source_refs=ev_refs,
        contains_redactions=contains_redactions,
        digest=str(fm.get("evidence_digest") or fm.get("digest") or ""),
        observed_commit=str(fm.get("evidence_observed_commit") or fm.get("observed_commit") or ""),
        producer=str(fm.get("evidence_producer") or fm.get("producer") or ""),
        verification_state=str(fm.get("evidence_verification_state") or "unverified"),
        verified_at=str(fm.get("evidence_verified_at") or ""),
        created_at=str(fm.get("evidence_created_at") or fm.get("evidence_timestamp") or ""),
    )

    # Parse Identity metadata
    identity = IdentityEnvelope(
        schema_version=str(fm.get("schema_version", "2.0")),
        project_id=str(fm.get("project_id", "")),
        repository=str(fm.get("repository") or fm.get("repository_id") or ""),
        commit_sha=str(fm.get("commit_sha", "")),
        branch=str(fm.get("branch") or fm.get("branch_or_ref") or ""),
        worktree=str(fm.get("worktree") or fm.get("worktree_id") or ""),
        team_id=str(fm.get("team_id", "")),
        agent_id=str(fm.get("agent_id", "")),
        agent_role=str(fm.get("agent_role", "")),
        session_id=str(fm.get("session_id", "")),
        conversation_id=str(fm.get("conversation_id", "")),
        task_id=str(fm.get("task_id", "")),
        checkpoint_id=str(fm.get("checkpoint_id", "")),
        producer=str(fm.get("producer", "")),
        producer_type=str(fm.get("producer_type", "")),
        producer_id=str(fm.get("producer_id", "")),
        producer_version=str(fm.get("producer_version", "")),
        reviewer=str(fm.get("reviewer") or fm.get("reviewer_id") or ""),
        harness=str(fm.get("harness", "")),
        model_provider=str(fm.get("model_provider", "")),
        model_id=str(fm.get("model_id", "")),
        authority_domain=str(fm.get("authority_domain", "")),
        authority_level=int(fm.get("authority_level", 0)),
        created_at=str(fm.get("created_at") or fm.get("timestamp") or ""),
        timestamp=str(fm.get("timestamp") or fm.get("created_at") or ""),
        observed_at=str(fm.get("observed_at", "")),
    )

    # Affected symbols / tests
    aff_syms = fm.get("affected_symbols", [])
    if isinstance(aff_syms, str):
        aff_syms = [aff_syms]
    aff_tests = fm.get("affected_tests", [])
    if isinstance(aff_tests, str):
        aff_tests = [aff_tests]

    known_keys = {
        "id", "kind", "status", "authority", "updated", "created_at", "confidence",
        "supersedes", "superseded_by", "freshness", "schema_version", "project_id",
        "repository", "repository_id", "commit_sha", "branch", "branch_or_ref",
        "worktree", "worktree_id", "team_id", "agent_id", "agent_role", "session_id",
        "conversation_id", "task_id", "checkpoint_id", "producer", "producer_type",
        "producer_id", "producer_version", "reviewer", "reviewer_id", "harness",
        "model_provider", "model_id", "authority_domain", "authority_level",
        "observed_at", "evidence_source_type", "evidence_authority_level", "evidence_observed_commit",
        "evidence_digest", "evidence_producer", "evidence_contains_redactions", "contains_redactions",
        "evidence_verification_state", "evidence_verified_at", "evidence_created_at",
        "evidence_observed_at", "evidence_timestamp", "evidence_source_refs", "source_type",
        "source_ref", "observed_commit", "digest", "timestamp", "affected_symbols", "affected_tests"
    }
    extra = {k: v for k, v in fm.items() if k not in known_keys}

    return KnowledgeRecord(
        id=str(fm.get("id", path.stem)),
        kind=kind,
        title=title,
        status=str(fm.get("status", "accepted")),
        authority=str(fm.get("authority", "code_observed")),
        updated=str(fm.get("updated") or fm.get("date") or today()),
        body=content_body,
        evidence=evidence,
        identity=identity,
        confidence=float(fm.get("confidence", 1.0)),
        created_at=str(fm.get("created_at") or fm.get("timestamp") or ""),
        scope=scope,
        affected_symbols=aff_syms,
        affected_tests=aff_tests,
        supersedes=str(fm.get("supersedes", "")),
        superseded_by=str(fm.get("superseded_by", "")),
        freshness=str(fm.get("freshness", "fresh")),
        path=rel_path,
        extra_frontmatter=extra,
    )


def serialize_markdown_record(record: KnowledgeRecord) -> str:
    return record.to_markdown()
