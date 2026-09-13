from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from context_forge.core.models import now_iso
from context_forge.store.paths import brain_paths, read_text


class TraceEdgeType(str, Enum):
    """Explicit typed traceability relationship edges."""
    SATISFIES = "SATISFIES"             # ADR / POL satisfies REQ
    IMPLEMENTS = "IMPLEMENTS"           # Source file/component implements ADR / REQ / POL
    SYMBOL_DEFINES = "SYMBOL_DEFINES"   # Symbol defined in component / file
    VERIFIES_WITH = "VERIFIES_WITH"     # Test suite/file verifies Component / Requirement / Decision
    OBSERVES = "OBSERVES"               # Technical record observes file / symbol
    DERIVED_FROM = "DERIVED_FROM"       # Record derived from parent requirement / decision


@dataclass
class TraceEdge:
    """A durable or resolved typed traceability edge with evidence and verification."""
    edge_type: TraceEdgeType
    source_id: str
    target_ref: str
    target_kind: str = "file"           # "file", "symbol", "test", "record"
    producer: str = "context-forge"
    provider: str = "native"           # "native" | "cbm"
    evidence: str = ""
    observed_commit_sha: str = ""
    verification_state: str = "unverified"  # "verified", "unverified", "possibly_stale", "stale", "contradicted"
    confidence: float = 0.5            # 0.5 for native heuristics, 0.95+ for CBM structural / AST / explicit link
    last_verified_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_type": self.edge_type.value,
            "source_id": self.source_id,
            "target_ref": self.target_ref,
            "target_kind": self.target_kind,
            "producer": self.producer,
            "provider": self.provider,
            "evidence": self.evidence,
            "observed_commit_sha": self.observed_commit_sha,
            "verification_state": self.verification_state,
            "confidence": self.confidence,
            "last_verified_at": self.last_verified_at,
        }


def is_test_path(path_str: str) -> bool:
    """Determine whether a path represents a test file without vague guessing."""
    norm = path_str.replace("\\", "/").lower()
    parts = norm.split("/")
    filename = parts[-1]

    # Explicit directory indicators
    if any(part in ("tests", "test", "spec", "specs") for part in parts[:-1]):
        return True

    # Explicit filename patterns
    if filename.startswith("test_") or filename.endswith("_test.py") or filename.endswith("_test.go"):
        return True
    if filename.endswith(".test.ts") or filename.endswith(".spec.ts") or filename.endswith(".test.js") or filename.endswith(".spec.js"):
        return True

    return False


def resolve_traceability_graph(repo: Path) -> list[dict[str, Any]]:
    """Traverse REQ, ADR, TRACE, and TECH records to construct the living traceability graph with typed edges."""
    p = brain_paths(repo)
    graph = []

    if not p["traceability"].exists():
        return graph

    for page in sorted(p["traceability"].glob("*.md")):
        text = read_text(page)
        source_m = re.search(r"## Source record\s*\n+- `([^`]+)`", text)
        paths_m = re.findall(r"## Related paths\s*\n+((?:- `[^`]+`\s*\n*)+)", text)

        source_id = source_m.group(1) if source_m else ""
        extracted_paths: list[str] = []
        if paths_m:
            extracted_paths = re.findall(r"- `([^`]+)`", paths_m[0])

        # Parse observed commit from frontmatter if present
        commit_m = re.search(r"(?m)^observed_commit:\s*([a-f0-9]{40})", text) or re.search(r"(?m)^commit_sha:\s*([a-f0-9]{40})", text)
        observed_sha = commit_m.group(1) if commit_m else ""

        edges: list[TraceEdge] = []
        tests: list[str] = []
        code_files: list[str] = []

        for item in extracted_paths:
            normalized = item.replace("\\", "/")
            target_file = repo / normalized
            file_exists = target_file.exists()

            if is_test_path(normalized):
                edge_type = TraceEdgeType.VERIFIES_WITH
                target_kind = "test"
                tests.append(normalized)
            else:
                edge_type = TraceEdgeType.IMPLEMENTS
                target_kind = "file"
                code_files.append(normalized)

            verification_state = "unverified" if file_exists else "stale"
            confidence = 0.5 if file_exists else 0.1
            evidence = f"Native path exists in worktree: {normalized}" if file_exists else f"Target path missing in worktree: {normalized}"

            edge = TraceEdge(
                edge_type=edge_type,
                source_id=source_id,
                target_ref=normalized,
                target_kind=target_kind,
                producer="context-forge",
                provider="native",
                evidence=evidence,
                observed_commit_sha=observed_sha,
                verification_state=verification_state,
                confidence=confidence,
                last_verified_at=now_iso() if file_exists else "",
            )
            edges.append(edge)

        overall_state = "verified" if (edges and all(e.verification_state == "verified" for e in edges)) else (
            "stale" if any(e.verification_state == "stale" for e in edges) else "unverified"
        )

        graph.append({
            "trace_id": page.stem,
            "source_id": source_id,
            "edges": [e.to_dict() for e in edges],
            "code_files": sorted(code_files),
            "tests": sorted(tests),
            "verification_state": overall_state,
        })

    return graph


def reconcile_traceability_with_provider(
    repo: Path,
    graph: list[dict[str, Any]],
    cbm_provider: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Reconcile traceability edges against external code intelligence when available.
    
    If CBM is available and verified:
        strengthen edge confidence and mark verified.
    If CBM indicates entity missing:
        mark edge stale/contradicted.
    If provider unavailable:
        preserve historical edge and keep native unverified state.
    """
    if cbm_provider is None or not getattr(cbm_provider, "is_available", lambda: False)():
        return graph

    for entry in graph:
        for edge in entry.get("edges", []):
            target_ref = edge.get("target_ref", "")
            target_path = repo / target_ref
            if not target_path.exists():
                edge["verification_state"] = "stale"
                edge["confidence"] = 0.0
                edge["evidence"] = "CBM reconciliation: target file not found on disk."
                continue

            try:
                # Query CBM for structural presence
                res = cbm_provider.query_code_intelligence(repo, query=target_path.stem)
                if res.status.value == "ok" and res.data:
                    edge["provider"] = "cbm"
                    edge["confidence"] = 0.95
                    edge["verification_state"] = "verified"
                    edge["last_verified_at"] = now_iso()
                    edge["evidence"] = "Structural relationship confirmed via CBM graph analysis."
            except Exception:
                # Provider error does not erase edge
                edge["verification_state"] = "unverified"

    return graph
