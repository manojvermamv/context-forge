from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
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
    ERROR = "error"                 # Crash, exception, unexpected failure


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
