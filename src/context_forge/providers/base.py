from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from context_forge.core.models import now_iso


class ProviderStatus(str, Enum):
    """Execution status for external and native intelligence providers."""
    OK = "ok"                       # Provider responded successfully with results
    NO_RESULTS = "no_results"       # Provider executed successfully but found 0 matches
    UNINDEXED = "unindexed"         # Project not indexed in external provider
    UNAVAILABLE = "unavailable"     # Binary/Daemon/Server not found or port closed
    DEGRADED = "degraded"           # Responded with partial results, timeout, or fallback
    UNAUTHORIZED = "unauthorized"   # 401/403 or missing/invalid secret token
    INCOMPATIBLE = "incompatible"   # Schema or version mismatch / missing endpoint
    MALFORMED = "malformed"         # Bad JSON or malformed response
    TIMEOUT = "timeout"             # Call timed out
    UNSUPPORTED = "unsupported"     # Requested capability/relationship not supported
    ERROR = "error"                 # Crash, exception, unexpected failure


class VerificationKind(str, Enum):
    """Classification of epistemic evidence used to verify a reference or edge."""
    EXPLICIT_LINK = "explicit_link"        # Explicit durable trace link (~1.00)
    STRUCTURAL_GRAPH = "structural_graph"  # CBM search_graph, trace_path, relationship query (~0.95)
    SYMBOL_GRAPH = "symbol_graph"          # CBM symbol existence query (~0.90)
    TEXT_SEARCH = "text_search"            # Grep / lexical search in files (~0.70)
    FILESYSTEM = "filesystem"              # Native filesystem path check only (~0.50)
    UNKNOWN = "unknown"                    # Missing or unverified (0.0)


def verification_confidence(kind: VerificationKind | str) -> float:
    """Centralized policy mapping verification evidence kinds to deterministic confidence scores."""
    mapping = {
        VerificationKind.EXPLICIT_LINK: 1.0,
        VerificationKind.STRUCTURAL_GRAPH: 0.95,
        VerificationKind.SYMBOL_GRAPH: 0.90,
        VerificationKind.TEXT_SEARCH: 0.70,
        VerificationKind.FILESYSTEM: 0.50,
        VerificationKind.UNKNOWN: 0.0,
    }
    if isinstance(kind, str):
        try:
            kind = VerificationKind(kind)
        except ValueError:
            return 0.0
    return mapping.get(kind, 0.0)


class TraceEdgeVerificationDomain(str, Enum):
    """Explicit verification authority domain for traceability edges."""
    PROJECT_GOVERNANCE = "PROJECT_GOVERNANCE"   # SATISFIES, DERIVED_FROM (Context Forge internal truth)
    CODE_STRUCTURE = "CODE_STRUCTURE"           # SYMBOL_DEFINES, IMPLEMENTS code, calls, extends
    TEST_EVIDENCE = "TEST_EVIDENCE"             # VERIFIES_WITH (test runner, test traces)
    FILESYSTEM = "FILESYSTEM"                   # OBSERVES, native file existence
    EXPLICIT_USER_LINK = "EXPLICIT_USER_LINK"   # Human explicit linking


@dataclass
class VerificationResult:
    """Explicit epistemic classification of reference and relationship verification."""
    reference_verified: bool = False
    symbol_verified: bool = False
    relationship_requested: bool = False
    relationship_verified: bool = False
    structurally_verified: bool = False
    verification_kind: str = VerificationKind.UNKNOWN.value
    reference_confidence: float = 0.0
    relationship_confidence: float = 0.0
    edge_confidence: float = 0.0
    diagnostic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_verified": self.reference_verified,
            "symbol_verified": self.symbol_verified,
            "relationship_requested": self.relationship_requested,
            "relationship_verified": self.relationship_verified,
            "structurally_verified": self.structurally_verified,
            "verification_kind": self.verification_kind,
            "reference_confidence": self.reference_confidence,
            "relationship_confidence": self.relationship_confidence,
            "edge_confidence": self.edge_confidence,
            "diagnostic": self.diagnostic,
        }



@dataclass
class ProviderCoverage:
    """Explicit epistemic coverage of structural intelligence providers.
    
    Prevents unindexed, partially parsed, or construct-limited provider queries
    from fabricating non-existence.
    """
    language: str = ""
    construct: str = ""
    indexed_commit: str = ""
    parse_state: str = "complete"  # "complete", "partial", "failed", "unindexed", "unknown"
    confidence: float = 1.0        # 1.0 = full analyzer coverage, < 1.0 = partial/heuristic

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "construct": self.construct,
            "indexed_commit": self.indexed_commit,
            "parse_state": self.parse_state,
            "confidence": self.confidence,
        }


@dataclass
class NegativeClaim:
    """Classification of negative evidence ('item absent' vs 'unverified due to coverage limits')."""
    definitive: bool = False
    proof_scope: str = "none"      # "none", "exact_node", "exact_edge", "full_graph", "file_parse"

    def to_dict(self) -> dict[str, Any]:
        return {
            "definitive": self.definitive,
            "proof_scope": self.proof_scope,
        }

@dataclass
class ProviderResult:
    """Structured response from an external or native intelligence provider."""
    status: ProviderStatus
    provider: str = ""
    provider_name: str = ""
    version: str = ""
    provider_version: str = ""
    capability: str = ""
    capabilities: list[str] = field(default_factory=list)
    data: Any = None
    diagnostic: str = ""
    diagnostic_code: str = ""
    diagnostic_message: str = ""
    execution_time_ms: float = 0.0
    fallback_used: bool = False
    degraded: bool = False
    source_timestamp: str = field(default_factory=now_iso)
    project_name: str = ""
    coverage: ProviderCoverage = field(default_factory=ProviderCoverage)
    negative_claim: NegativeClaim = field(default_factory=NegativeClaim)

    def __post_init__(self) -> None:
        if not self.provider_name and self.provider:
            self.provider_name = self.provider
        elif not self.provider and self.provider_name:
            self.provider = self.provider_name

        if not self.provider_version and self.version:
            self.provider_version = self.version
        elif not self.version and self.provider_version:
            self.version = self.provider_version

        if not self.diagnostic_message and self.diagnostic:
            self.diagnostic_message = self.diagnostic
        elif not self.diagnostic and self.diagnostic_message:
            self.diagnostic = self.diagnostic_message

        if self.status in (ProviderStatus.DEGRADED, ProviderStatus.TIMEOUT):
            self.degraded = True

    def is_ok(self) -> bool:
        return self.status in (ProviderStatus.OK, ProviderStatus.NO_RESULTS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "provider_name": self.provider_name,
            "version": self.version,
            "provider_version": self.provider_version,
            "capability": self.capability,
            "capabilities": self.capabilities,
            "diagnostic": self.diagnostic,
            "diagnostic_code": self.diagnostic_code,
            "diagnostic_message": self.diagnostic_message,
            "data_count": len(self.data) if isinstance(self.data, (list, dict)) else (1 if self.data else 0),
            "execution_time_ms": self.execution_time_ms,
            "fallback_used": self.fallback_used,
            "degraded": self.degraded,
            "source_timestamp": self.source_timestamp,
            "project_name": self.project_name,
            "coverage": self.coverage.to_dict(),
            "negative_claim": self.negative_claim.to_dict(),
        }


class CodeIntelligenceProvider(ABC):
    """Abstract interface for code structural knowledge."""

    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'native_code_map', 'codebase_memory_mcp')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is configured and available."""
        pass

    def check_health(self) -> ProviderResult:
        """Inspect provider health, version, and capabilities."""
        if self.is_available():
            return ProviderResult(status=ProviderStatus.OK, provider=self.name())
        return ProviderResult(status=ProviderStatus.UNAVAILABLE, provider=self.name(), diagnostic="Provider unavailable")

    @abstractmethod
    def get_symbol_map(self, repo_path: str) -> str:
        """Return codebase structure and key symbols."""
        pass

    def query_impact(
        self,
        repo_path: Path | str,
        query: str,
        paths: Optional[list[str]] = None,
        scope_paths: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Query symbols, callers, and impact for a task (backward-compatible list)."""
        res = self.query_impact_result(repo_path, query, paths=paths, scope_paths=scope_paths, **kwargs)
        return list(res.data) if isinstance(res.data, list) else []

    @abstractmethod
    def query_impact_result(
        self,
        repo_path: Path | str,
        query: str,
        paths: Optional[list[str]] = None,
        scope_paths: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> ProviderResult:
        """Query symbols, callers, and impact returning structured ProviderResult.
        
        Args:
            repo_path: Explicit filesystem root of target repository.
            query: Task query string.
            paths: Optional list of relevant scope paths.
            scope_paths: Optional alias for paths.
        """
        pass

    @abstractmethod
    def verify_reference(
        self,
        repo_path: Path | str,
        path: str,
        symbol: Optional[str] = None,
        relationship: Optional[str] = None,
        target_symbol: Optional[str] = None,
        target_path: Optional[str] = None,
    ) -> ProviderResult:
        """Verify structural presence of a target file, symbol, or relationship."""
        pass


class ExperienceProvider(ABC):
    """Abstract interface for agent runtime lessons, tool failures, and history."""

    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'native_experience', 'agentmemory')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is configured and available."""
        pass

    def check_health(self) -> ProviderResult:
        """Inspect provider health, version, and capabilities."""
        if self.is_available():
            return ProviderResult(status=ProviderStatus.OK, provider=self.name())
        return ProviderResult(status=ProviderStatus.UNAVAILABLE, provider=self.name(), diagnostic="Provider unavailable")

    def recall_lessons(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Recall relevant lessons, past failures, or workflow hints (backward-compatible list)."""
        res = self.recall_lessons_result(query, limit)
        return list(res.data) if isinstance(res.data, list) else []

    @abstractmethod
    def recall_lessons_result(self, query: str, limit: int = 5) -> ProviderResult:
        """Recall relevant lessons returning structured ProviderResult."""
        pass
