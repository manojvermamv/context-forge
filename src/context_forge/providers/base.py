from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ProviderStatus(str, Enum):
    """Execution status for external and native intelligence providers."""
    OK = "ok"                       # Provider responded successfully with results
    NO_RESULTS = "no_results"       # Provider executed successfully but found 0 matches
    UNAVAILABLE = "unavailable"     # Binary/Daemon/Server not found or port closed
    DEGRADED = "degraded"           # Responded with partial results, timeout, or fallback
    UNAUTHORIZED = "unauthorized"   # 401/403 or missing/invalid secret token
    INCOMPATIBLE = "incompatible"   # Schema or version mismatch / missing endpoint
    ERROR = "error"                 # Crash, exception, malformed output


@dataclass
class ProviderResult:
    """Structured response from an external or native intelligence provider."""
    status: ProviderStatus
    provider: str
    version: str = ""
    capabilities: list[str] = field(default_factory=list)
    data: Any = None
    diagnostic: str = ""
    execution_time_ms: float = 0.0

    def is_ok(self) -> bool:
        return self.status in (ProviderStatus.OK, ProviderStatus.NO_RESULTS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "version": self.version,
            "capabilities": self.capabilities,
            "diagnostic": self.diagnostic,
            "data_count": len(self.data) if isinstance(self.data, (list, dict)) else (1 if self.data else 0),
            "execution_time_ms": self.execution_time_ms,
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

    def query_impact(self, query: str, paths: list[str]) -> list[dict[str, Any]]:
        """Query symbols, callers, and impact for a task (backward-compatible list)."""
        res = self.query_impact_result(query, paths)
        return list(res.data) if isinstance(res.data, list) else []

    @abstractmethod
    def query_impact_result(self, query: str, paths: list[str]) -> ProviderResult:
        """Query symbols, callers, and impact returning structured ProviderResult."""
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

