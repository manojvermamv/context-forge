from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from context_forge.providers.base import ExperienceProvider


class AgentMemoryProvider(ExperienceProvider):
    """Optional accelerator: queries AgentMemory for procedural lessons, past errors, and workflows."""

    def __init__(self, endpoint_url: str | None = None):
        self.endpoint_url = endpoint_url or os.environ.get("AGENT_MEMORY_URL") or "http://localhost:3111"

    def name(self) -> str:
        return "agentmemory"

    def is_available(self) -> bool:
        if not self.endpoint_url:
            return False
        try:
            req = urllib.request.Request(f"{self.endpoint_url.rstrip('/')}/health", headers={"User-Agent": "context-forge"})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def recall_lessons(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        try:
            payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.endpoint_url.rstrip('/')}/memory/search",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "context-forge"},
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                memories = data.get("memories", [])
                return [{"finding": m.get("content", ""), "source": "agentmemory"} for m in memories[:limit]]
        except Exception:
            return []
