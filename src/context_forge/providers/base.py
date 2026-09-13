from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


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

    @abstractmethod
    def get_symbol_map(self, repo_path: str) -> str:
        """Return codebase structure and key symbols."""
        pass

    @abstractmethod
    def query_impact(self, query: str, paths: list[str]) -> list[dict[str, Any]]:
        """Query symbols, callers, and impact for a task."""
        pass


class ExperienceProvider(ABC):
    """Abstract interface for agent runtime lessons, tool failures, and history."""

    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'native_session_history', 'agentmemory')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is configured and available."""
        pass

    @abstractmethod
    def recall_lessons(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Recall relevant lessons, past failures, or workflow hints."""
        pass
