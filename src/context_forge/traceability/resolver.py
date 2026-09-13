from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from context_forge.core.models import now_iso
from context_forge.store.paths import brain_paths, read_text
from context_forge.providers.base import (
    ProviderStatus,
    VerificationKind,
    verification_confidence,
)


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
    verification_state: str = "unverified"  # "verified", "unverified", "partially_verified", "possibly_stale", "stale", "contradicted"
    confidence: float = 0.5            # explicit confidence score based on verification kind
    last_verified_at: str = ""
    symbol: Optional[str] = None
    relationship: Optional[str] = None
    verification_kind: str = VerificationKind.UNKNOWN.value
    structurally_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_type": self.edge_type.value if hasattr(self.edge_type, "value") else str(self.edge_type),
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
            "symbol": self.symbol,
            "relationship": self.relationship,
            "verification_kind": self.verification_kind,
            "structurally_verified": self.structurally_verified,
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


def _recompute_graph_aggregate_state(graph: list[dict[str, Any]]) -> None:
    """Recompute each graph entry verification_state from its edges:
    - any stale or contradicted edge => stale
    - all required edges verified => verified
    - mix of verified and unverified => partially_verified
    - all unverified or empty => unverified
    """
    for entry in graph:
        edges = entry.get("edges", [])
        if not edges:
            entry["verification_state"] = "unverified"
        elif any(e.get("verification_state") in ("stale", "contradicted") for e in edges):
            entry["verification_state"] = "stale"
        elif all(e.get("verification_state") == "verified" for e in edges):
            entry["verification_state"] = "verified"
        elif any(e.get("verification_state") == "verified" for e in edges):
            entry["verification_state"] = "partially_verified"
        else:
            entry["verification_state"] = "unverified"


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
            v_kind = VerificationKind.FILESYSTEM.value if file_exists else VerificationKind.UNKNOWN.value
            confidence = verification_confidence(v_kind)
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
                symbol=None,
                relationship=edge_type.value if hasattr(edge_type, "value") else str(edge_type),
                verification_kind=v_kind,
                structurally_verified=False,
            )
            edges.append(edge)

        graph.append({
            "trace_id": page.stem,
            "source_id": source_id,
            "edges": [e.to_dict() for e in edges],
            "code_files": sorted(code_files),
            "tests": sorted(tests),
            "verification_state": "unverified",
        })

    _recompute_graph_aggregate_state(graph)
    return graph


def reconcile_traceability_with_provider(
    repo: Path,
    graph: list[dict[str, Any]],
    provider: Optional[Any] = None,
    cbm_provider: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Reconcile traceability edges against external code intelligence when available.
    
    If provider confirms structural relationship:
        strengthen edge confidence and mark verified.
    If provider indicates entity missing:
        mark edge stale/contradicted.
    If provider is unavailable or unindexed:
        preserve historical edge and keep native unverified state with honest diagnostic.
    Recomputes aggregate entry verification_state.
    """
    prov = provider if provider is not None else cbm_provider
    if prov is None or not getattr(prov, "is_available", lambda: False)():
        _recompute_graph_aggregate_state(graph)
        return graph

    provider_name = getattr(prov, "name", lambda: "provider")()

    for entry in graph:
        for edge in entry.get("edges", []):
            target_ref = edge.get("target_ref", "")
            symbol = edge.get("symbol")
            relationship = edge.get("relationship") or edge.get("edge_type")

            if not hasattr(prov, "verify_reference"):
                raise TypeError(
                    f"Provider '{provider_name}' violates CodeIntelligenceProvider contract: "
                    f"missing required method 'verify_reference'"
                )

            try:
                res = prov.verify_reference(
                    repo_path=repo,
                    path=target_ref,
                    symbol=symbol,
                    relationship=relationship,
                )
            except (TimeoutError, OSError) as exc:
                edge["verification_state"] = "unverified"
                edge["evidence"] = f"Provider '{provider_name}' runtime communication failure: {exc}"
                continue

            if res.status == ProviderStatus.UNSUPPORTED:
                edge["verification_state"] = "unverified"
                edge["verification_kind"] = VerificationKind.UNKNOWN.value
                edge["structurally_verified"] = False
                edge["evidence"] = res.diagnostic or f"Provider '{provider_name}' does not support verifying requested relationship."
            elif res.is_ok() and res.data:
                d = res.data if isinstance(res.data, dict) else {}
                v_kind = d.get("verification_kind")
                if not v_kind:
                    if d.get("structurally_verified"):
                        v_kind = VerificationKind.STRUCTURAL_GRAPH.value
                    elif d.get("symbol"):
                        v_kind = VerificationKind.SYMBOL_GRAPH.value
                    else:
                        v_kind = VerificationKind.FILESYSTEM.value

                structurally_verified = bool(d.get("structurally_verified", False))
                conf = float(d.get("confidence", verification_confidence(v_kind)))

                edge["provider"] = provider_name
                edge["verification_kind"] = v_kind
                edge["structurally_verified"] = structurally_verified
                edge["confidence"] = conf

                if structurally_verified:
                    edge["verification_state"] = "verified"
                    edge["last_verified_at"] = now_iso()
                    edge["evidence"] = res.diagnostic or f"Structural relationship confirmed via {provider_name}."
                elif v_kind == VerificationKind.SYMBOL_GRAPH.value:
                    edge["verification_state"] = "verified"
                    edge["last_verified_at"] = now_iso()
                    edge["evidence"] = res.diagnostic or f"Symbol confirmed in {provider_name} graph."
                elif v_kind == VerificationKind.FILESYSTEM.value:
                    # Filesystem presence alone is NOT structural verification
                    edge["verification_state"] = "unverified"
                    edge["evidence"] = res.diagnostic or f"File path exists on disk; structural relationship unverified by {provider_name}."
                else:
                    edge["verification_state"] = "unverified"
                    edge["evidence"] = res.diagnostic or f"Verification degraded via {provider_name}."
            elif res.status == ProviderStatus.NO_RESULTS:
                edge["verification_state"] = "stale"
                edge["confidence"] = 0.0
                edge["structurally_verified"] = False
                edge["verification_kind"] = VerificationKind.UNKNOWN.value
                edge["evidence"] = res.diagnostic or f"Reference '{target_ref}' missing in {provider_name}."
            elif res.status in (ProviderStatus.UNAVAILABLE, ProviderStatus.UNINDEXED):
                edge["verification_state"] = "unverified"
                edge["evidence"] = f"{provider_name} {res.status.value}: historical edge preserved."
            else:
                edge["verification_state"] = "unverified"
                edge["evidence"] = res.diagnostic or f"{provider_name} returned status '{res.status.value}'."

    _recompute_graph_aggregate_state(graph)
    return graph
