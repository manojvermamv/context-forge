from __future__ import annotations

from pathlib import Path
from typing import Any
from context_forge.store.paths import read_text, brain_paths
from context_forge.providers.base import ExperienceProvider, ProviderResult, ProviderStatus


class NativeExperienceProvider(ExperienceProvider):
    """Recalls past lessons from local approved log.md and concept summaries."""

    def __init__(self, repo_path: Path):
        self.repo = repo_path

    def name(self) -> str:
        return "native_experience"

    def is_available(self) -> bool:
        return (self.repo / ".brain" / "log.md").exists()

    def check_health(self) -> ProviderResult:
        if self.is_available():
            return ProviderResult(
                status=ProviderStatus.OK,
                provider=self.name(),
                version="2.0",
                capabilities=["approved_log_history"],
                diagnostic="Native approved log available.",
            )
        return ProviderResult(
            status=ProviderStatus.UNAVAILABLE,
            provider=self.name(),
            diagnostic="Local .brain/log.md not found.",
        )

    def recall_lessons_result(self, query: str, limit: int = 5) -> ProviderResult:
        if not self.is_available():
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                data=[],
                diagnostic="Local .brain/log.md not found.",
            )

        p = brain_paths(self.repo)
        log_text = read_text(p["log"])
        query_words = [w.lower() for w in query.split() if len(w) >= 3]
        results = []

        for line in log_text.splitlines():
            line_str = line.strip()
            if not line_str.startswith("- ("):
                continue
            line_lower = line_str.lower()
            if any(w in line_lower for w in query_words) or not query_words:
                results.append({"finding": line_str, "source": "approved_log"})
                if len(results) >= limit:
                    break

        status = ProviderStatus.OK if results else ProviderStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.name(),
            data=results,
            diagnostic=f"Found {len(results)} past lessons in local log.",
        )

