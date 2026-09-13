from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from context_forge.providers.base import ExperienceProvider, ProviderResult, ProviderStatus


class AgentMemoryProvider(ExperienceProvider):
    """Integrates with stock AgentMemory REST API (http://localhost:3111) for experiential knowledge."""

    def __init__(self, endpoint_url: str | None = None, secret: str | None = None):
        self.endpoint_url = (
            endpoint_url
            or os.environ.get("AGENTMEMORY_URL")
            or os.environ.get("AGENT_MEMORY_URL")
            or "http://localhost:3111"
        ).rstrip("/")
        self.secret = (
            secret
            or os.environ.get("AGENTMEMORY_SECRET")
            or os.environ.get("AGENT_MEMORY_SECRET")
            or ""
        )

    def name(self) -> str:
        return "agentmemory"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ContextForge/2.0",
        }
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def check_health(self) -> ProviderResult:
        """Query GET /agentmemory/health to establish status, version, and auth."""
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                f"{self.endpoint_url}/agentmemory/health",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if resp.status == 200:
                    try:
                        body = json.loads(resp.read().decode("utf-8"))
                    except Exception:
                        body = {}
                    return ProviderResult(
                        status=ProviderStatus.OK,
                        provider=self.name(),
                        version=str(body.get("version", "1.0")),
                        capabilities=body.get("capabilities", ["smart-search", "lessons", "patterns"]),
                        diagnostic="AgentMemory healthy and connected.",
                        execution_time_ms=elapsed_ms,
                    )
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if exc.code in (401, 403):
                return ProviderResult(
                    status=ProviderStatus.UNAUTHORIZED,
                    provider=self.name(),
                    diagnostic=f"AgentMemory authentication failed (HTTP {exc.code}). AGENTMEMORY_SECRET required.",
                    execution_time_ms=elapsed_ms,
                )
            elif exc.code == 404:
                return ProviderResult(
                    status=ProviderStatus.INCOMPATIBLE,
                    provider=self.name(),
                    diagnostic=f"Endpoint /agentmemory/health not found on {self.endpoint_url}. Verify server version.",
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic=f"AgentMemory HTTP {exc.code}: {exc.reason}",
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                diagnostic=f"AgentMemory unreachable at {self.endpoint_url}: {exc}",
                execution_time_ms=elapsed_ms,
            )
        return ProviderResult(
            status=ProviderStatus.UNAVAILABLE,
            provider=self.name(),
            diagnostic=f"AgentMemory unreachable at {self.endpoint_url}",
        )

    def is_available(self) -> bool:
        return self.check_health().is_ok()

    def recall_lessons_result(self, query: str, limit: int = 5) -> ProviderResult:
        """Recall lessons using POST /agentmemory/smart-search."""
        t0 = time.monotonic()
        payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")

        for endpoint in ("/agentmemory/smart-search", "/agentmemory/search"):
            try:
                req = urllib.request.Request(
                    f"{self.endpoint_url}{endpoint}",
                    data=payload,
                    headers=self._headers(),
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    raw_data = json.loads(resp.read().decode("utf-8"))

                    # Handle list of items or {memories: [...]} or {results: [...]}
                    items = []
                    if isinstance(raw_data, list):
                        items = raw_data
                    elif isinstance(raw_data, dict):
                        items = raw_data.get("results") or raw_data.get("memories") or raw_data.get("lessons") or []

                    mapped = []
                    for m in items:
                        finding = ""
                        if isinstance(m, str):
                            finding = m
                        elif isinstance(m, dict):
                            finding = m.get("content") or m.get("finding") or m.get("lesson") or m.get("text") or str(m)
                        if finding:
                            mapped.append({
                                "finding": finding,
                                "source": "agentmemory",
                                "id": m.get("id", "") if isinstance(m, dict) else "",
                            })

                    if not mapped:
                        return ProviderResult(
                            status=ProviderStatus.NO_RESULTS,
                            provider=self.name(),
                            data=[],
                            diagnostic=f"AgentMemory smart-search returned 0 results for '{query}'",
                            execution_time_ms=elapsed_ms,
                        )

                    return ProviderResult(
                        status=ProviderStatus.OK,
                        provider=self.name(),
                        data=mapped[:limit],
                        diagnostic=f"Retrieved {len(mapped[:limit])} memories from AgentMemory.",
                        execution_time_ms=elapsed_ms,
                    )

            except urllib.error.HTTPError as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if exc.code in (401, 403):
                    return ProviderResult(
                        status=ProviderStatus.UNAUTHORIZED,
                        provider=self.name(),
                        data=[],
                        diagnostic=f"AgentMemory authentication failed on {endpoint} (HTTP {exc.code}).",
                        execution_time_ms=elapsed_ms,
                    )
                elif exc.code == 404:
                    continue  # Try next endpoint
                return ProviderResult(
                    status=ProviderStatus.ERROR,
                    provider=self.name(),
                    data=[],
                    diagnostic=f"AgentMemory HTTP {exc.code} on {endpoint}: {exc.reason}",
                    execution_time_ms=elapsed_ms,
                )
            except urllib.error.URLError as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.UNAVAILABLE,
                    provider=self.name(),
                    data=[],
                    diagnostic=f"AgentMemory server unreachable at {self.endpoint_url}: {exc}",
                    execution_time_ms=elapsed_ms,
                )
            except TimeoutError:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.DEGRADED,
                    provider=self.name(),
                    data=[],
                    diagnostic="AgentMemory request timed out after 2.0s.",
                    execution_time_ms=elapsed_ms,
                )
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.ERROR,
                    provider=self.name(),
                    data=[],
                    diagnostic=f"AgentMemory unexpected error: {exc}",
                    execution_time_ms=elapsed_ms,
                )

        return ProviderResult(
            status=ProviderStatus.INCOMPATIBLE,
            provider=self.name(),
            data=[],
            diagnostic="AgentMemory search endpoints (/agentmemory/smart-search, /agentmemory/search) not found.",
        )

