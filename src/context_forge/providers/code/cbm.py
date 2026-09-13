from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from context_forge.providers.base import CodeIntelligenceProvider


class CodebaseMemoryMCPProvider(CodeIntelligenceProvider):
    """Optional accelerator: queries Codebase-Memory-MCP for call graphs, AST symbols, and impact."""

    def __init__(self, endpoint_url: str | None = None):
        self.endpoint_url = endpoint_url or os.environ.get("CBM_HTTP_URL") or os.environ.get("CODEBASE_MEMORY_URL")

    def name(self) -> str:
        return "codebase_memory_mcp"

    def is_available(self) -> bool:
        if not self.endpoint_url:
            return False
        try:
            req = urllib.request.Request(f"{self.endpoint_url.rstrip('/')}/health", headers={"User-Agent": "context-forge"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_symbol_map(self, repo_path: str) -> str:
        return ""

    def query_impact(self, query: str, paths: list[str]) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        try:
            payload = json.dumps({"query": query, "paths": paths}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.endpoint_url.rstrip('/')}/impact",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "context-forge"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return list(data.get("impact", []))
        except Exception:
            return []
