from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from context_forge.core.evidence import screen_secrets
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
        """Query GET /agentmemory/health to establish status, version, and auth strictly.

        Identity contract:
          Validates either:
          1. Explicit service identity ('agentmemory' or 'agent-memory' in service/name/app fields), OR
          2. Capability-based identity for compatible versions (e.g. 'smart-search', 'memories', 'search'),
          while strictly rejecting any explicitly conflicting service name (e.g. 'nginx').
        """
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                f"{self.endpoint_url}/agentmemory/health",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    try:
                        body = json.loads(raw_text)
                    except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
                        return ProviderResult(
                            status=ProviderStatus.MALFORMED,
                            provider=self.name(),
                            diagnostic_code="MALFORMED_JSON",
                            diagnostic=f"AgentMemory /health returned invalid JSON: {json_err}",
                            execution_time_ms=elapsed_ms,
                        )

                    if not isinstance(body, dict):
                        return ProviderResult(
                            status=ProviderStatus.MALFORMED,
                            provider=self.name(),
                            diagnostic_code="INVALID_RESPONSE_TYPE",
                            diagnostic="AgentMemory /health did not return a JSON object.",
                            execution_time_ms=elapsed_ms,
                        )

                    # Validate that response exhibits real compatible structure
                    has_status = "status" in body
                    raw_status = str(body.get("status", "")).lower()

                    service_val = str(body.get("service") or body.get("name") or body.get("app") or "").lower()
                    has_service_match = "agentmemory" in service_val or "agent-memory" in service_val
                    has_service_conflict = bool(service_val) and not has_service_match

                    # If service name explicitly conflicts (e.g. {"service": "nginx"}):
                    if has_service_conflict:
                        return ProviderResult(
                            status=ProviderStatus.INCOMPATIBLE,
                            provider=self.name(),
                            diagnostic_code="INCOMPATIBLE_SERVICE_IDENTITY",
                            diagnostic=f"Service identity '{service_val}' does not match AgentMemory.",
                            execution_time_ms=elapsed_ms,
                        )

                    caps = list(body.get("capabilities", [])) if isinstance(body.get("capabilities"), list) else []
                    has_am_caps = any(c in ("smart-search", "search", "memories", "memory") for c in caps)

                    # Strong identity check: must exhibit agentmemory service OR agentmemory-specific capability
                    if not (has_service_match or has_am_caps):
                        return ProviderResult(
                            status=ProviderStatus.INCOMPATIBLE,
                            provider=self.name(),
                            diagnostic_code="INCOMPATIBLE_HEALTH_RESPONSE",
                            diagnostic="Response lacks AgentMemory identity: missing service 'agentmemory' or AgentMemory capabilities.",
                            execution_time_ms=elapsed_ms,
                        )

                    if has_status and raw_status in ("down", "error", "unhealthy", "failed"):
                        return ProviderResult(
                            status=ProviderStatus.ERROR,
                            provider=self.name(),
                            diagnostic_code="HEALTH_STATUS_DOWN",
                            diagnostic=f"AgentMemory reported unhealthy status: {body.get('status')}",
                            execution_time_ms=elapsed_ms,
                        )

                    if has_status and raw_status in ("degraded", "warning", "partial"):
                        return ProviderResult(
                            status=ProviderStatus.DEGRADED,
                            provider=self.name(),
                            version=str(body.get("version", "")),
                            capabilities=caps,
                            diagnostic_code="HEALTH_DEGRADED",
                            diagnostic=f"AgentMemory operational in degraded mode: {body.get('status')}",
                            execution_time_ms=elapsed_ms,
                        )

                    version = str(body.get("version", "")) if bool(body.get("version")) else ""

                    return ProviderResult(
                        status=ProviderStatus.OK,
                        provider=self.name(),
                        version=version,
                        capabilities=caps,
                        diagnostic="AgentMemory healthy and connected.",
                        execution_time_ms=elapsed_ms,
                    )
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if exc.code in (401, 403):
                return ProviderResult(
                    status=ProviderStatus.UNAUTHORIZED,
                    provider=self.name(),
                    diagnostic_code="AUTH_FAILED",
                    diagnostic=f"AgentMemory authentication failed (HTTP {exc.code}). Valid AGENTMEMORY_SECRET required.",
                    execution_time_ms=elapsed_ms,
                )
            elif exc.code == 404:
                # Check /agentmemory/livez as alternative liveness endpoint
                try:
                    req_lz = urllib.request.Request(
                        f"{self.endpoint_url}/agentmemory/livez",
                        headers=self._headers(),
                    )
                    with urllib.request.urlopen(req_lz, timeout=1.5) as resp_lz:
                        if resp_lz.status == 200:
                            return ProviderResult(
                                status=ProviderStatus.OK,
                                provider=self.name(),
                                diagnostic="AgentMemory alive via /agentmemory/livez.",
                                execution_time_ms=(time.monotonic() - t0) * 1000,
                            )
                except Exception:
                    pass
                return ProviderResult(
                    status=ProviderStatus.INCOMPATIBLE,
                    provider=self.name(),
                    diagnostic_code="HEALTH_ENDPOINT_NOT_FOUND",
                    diagnostic=f"Endpoint /agentmemory/health not found on {self.endpoint_url}. Verify server version.",
                    execution_time_ms=elapsed_ms,
                )
            elif exc.code == 503:
                try:
                    err_body_raw = exc.read().decode("utf-8")
                    err_json = json.loads(err_body_raw) if err_body_raw else {}
                except Exception:
                    err_json = {}
                svc = str(err_json.get("service") or err_json.get("name") or "").lower()
                if "agentmemory" in svc or "agent-memory" in svc or err_json.get("status") == "degraded":
                    return ProviderResult(
                        status=ProviderStatus.DEGRADED,
                        provider=self.name(),
                        diagnostic_code="HTTP_503_DEGRADED",
                        diagnostic="AgentMemory returned HTTP 503 (temporarily degraded).",
                        execution_time_ms=elapsed_ms,
                    )
                return ProviderResult(
                    status=ProviderStatus.ERROR,
                    provider=self.name(),
                    diagnostic_code="HTTP_503",
                    diagnostic=screen_secrets(f"AgentMemory HTTP 503: {exc.reason}"),
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code=f"HTTP_{exc.code}",
                diagnostic=screen_secrets(f"AgentMemory HTTP {exc.code}: {exc.reason}"),
                execution_time_ms=elapsed_ms,
            )
        except urllib.error.URLError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                diagnostic_code="SERVER_UNREACHABLE",
                diagnostic=screen_secrets(f"AgentMemory unreachable at {self.endpoint_url}: {exc.reason}"),
                execution_time_ms=elapsed_ms,
            )
        except TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProviderResult(
                status=ProviderStatus.TIMEOUT,
                provider=self.name(),
                diagnostic_code="HEALTH_TIMEOUT",
                diagnostic="AgentMemory /health timed out after 1.5s.",
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code="HEALTH_EXCEPTION",
                diagnostic=screen_secrets(f"AgentMemory health check error: {exc}"),
                execution_time_ms=elapsed_ms,
            )
        return ProviderResult(
            status=ProviderStatus.UNAVAILABLE,
            provider=self.name(),
            diagnostic="AgentMemory unreachable at configured URL.",
        )

    def is_available(self) -> bool:
        health = self.check_health()
        return health.status in (ProviderStatus.OK, ProviderStatus.DEGRADED, ProviderStatus.NO_RESULTS)

    def recall_lessons_result(self, query: str, limit: int = 5) -> ProviderResult:
        """Recall lessons using POST /agentmemory/smart-search with auth and fallback."""
        t0 = time.monotonic()
        payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")

        for endpoint in ("/agentmemory/smart-search", "/agentmemory/search"):
            try:
                req = urllib.request.Request(
                    f"{self.endpoint_url}{endpoint}",
                    data=payload,
                    headers=self._headers(),
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    raw_text = resp.read().decode("utf-8")
                    try:
                        raw_data = json.loads(raw_text)
                    except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
                        return ProviderResult(
                            status=ProviderStatus.MALFORMED,
                            provider=self.name(),
                            data=[],
                            diagnostic_code="MALFORMED_SEARCH_JSON",
                            diagnostic=f"AgentMemory returned malformed JSON from {endpoint}: {json_err}",
                            execution_time_ms=elapsed_ms,
                        )

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
                            diagnostic=f"AgentMemory returned 0 results for '{query}'",
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
                        diagnostic_code="SEARCH_UNAUTHORIZED",
                        diagnostic=f"AgentMemory authentication rejected on {endpoint} (HTTP {exc.code}).",
                        execution_time_ms=elapsed_ms,
                    )
                elif exc.code == 404:
                    continue  # Try next endpoint
                return ProviderResult(
                    status=ProviderStatus.ERROR,
                    provider=self.name(),
                    data=[],
                    diagnostic_code=f"HTTP_{exc.code}",
                    diagnostic=screen_secrets(f"AgentMemory HTTP {exc.code} on {endpoint}: {exc.reason}"),
                    execution_time_ms=elapsed_ms,
                )
            except urllib.error.URLError as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.UNAVAILABLE,
                    provider=self.name(),
                    data=[],
                    diagnostic_code="SEARCH_SERVER_UNREACHABLE",
                    diagnostic=screen_secrets(f"AgentMemory server unreachable at {self.endpoint_url}: {exc.reason}"),
                    execution_time_ms=elapsed_ms,
                )
            except TimeoutError:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.TIMEOUT,
                    provider=self.name(),
                    data=[],
                    diagnostic_code="SEARCH_TIMEOUT",
                    diagnostic=f"AgentMemory request timed out after 3.0s on {endpoint}.",
                    execution_time_ms=elapsed_ms,
                )
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return ProviderResult(
                    status=ProviderStatus.ERROR,
                    provider=self.name(),
                    data=[],
                    diagnostic_code="SEARCH_EXCEPTION",
                    diagnostic=screen_secrets(f"AgentMemory unexpected search error: {exc}"),
                    execution_time_ms=elapsed_ms,
                )

        return ProviderResult(
            status=ProviderStatus.INCOMPATIBLE,
            provider=self.name(),
            data=[],
            diagnostic="AgentMemory search endpoints (/agentmemory/smart-search, /agentmemory/search) not found.",
        )

    def remember(
        self,
        content: str,
        concepts: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderResult:
        """Store an experiential lesson via POST /agentmemory/remember."""
        t0 = time.monotonic()
        data: dict[str, Any] = {"content": content}
        if concepts:
            data["concepts"] = concepts
        if metadata:
            data["metadata"] = metadata

        payload = json.dumps(data).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self.endpoint_url}/agentmemory/remember",
                data=payload,
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                raw_text = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(raw_text)
                except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
                    return ProviderResult(
                        status=ProviderStatus.MALFORMED,
                        provider=self.name(),
                        diagnostic_code="MALFORMED_JSON",
                        diagnostic=f"AgentMemory /remember returned malformed JSON: {json_err}",
                        execution_time_ms=elapsed_ms,
                    )

                if not isinstance(parsed, (dict, list)):
                    return ProviderResult(
                        status=ProviderStatus.MALFORMED,
                        provider=self.name(),
                        diagnostic_code="INVALID_RESPONSE_TYPE",
                        diagnostic="AgentMemory /remember returned unexpected non-JSON response structure.",
                        execution_time_ms=elapsed_ms,
                    )

                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    data=parsed,
                    diagnostic="Memory stored successfully in AgentMemory.",
                    execution_time_ms=elapsed_ms,
                )
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if exc.code in (401, 403):
                return ProviderResult(
                    status=ProviderStatus.UNAUTHORIZED,
                    provider=self.name(),
                    diagnostic_code="REMEMBER_UNAUTHORIZED",
                    diagnostic=f"AgentMemory /remember unauthorized (HTTP {exc.code}).",
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code=f"HTTP_{exc.code}",
                diagnostic=screen_secrets(f"AgentMemory /remember HTTP {exc.code}: {exc.reason}"),
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code="REMEMBER_EXCEPTION",
                diagnostic=screen_secrets(f"Failed to call AgentMemory /remember: {exc}"),
                execution_time_ms=elapsed_ms,
            )
