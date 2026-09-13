from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from context_forge.providers.base import CodeIntelligenceProvider, ProviderResult, ProviderStatus


class CodebaseMemoryMCPProvider(CodeIntelligenceProvider):
    """Integrates with stock Codebase-Memory-MCP via its CLI surface (codebase-memory-mcp cli <tool> <json>)."""

    DEFAULT_TOOLS = [
        "search_graph",
        "trace_path",
        "detect_changes",
        "search_code",
        "query_graph",
    ]

    def __init__(self, executable_path: str | None = None, endpoint_url: str | None = None):
        self.executable_path = (
            executable_path
            or os.environ.get("CBM_PATH")
            or os.environ.get("CODEBASE_MEMORY_PATH")
        )
        self.endpoint_url = endpoint_url or os.environ.get("CBM_HTTP_URL")

    def name(self) -> str:
        return "codebase_memory_mcp"

    def _resolve_executable(self) -> Optional[Path]:
        """Locate codebase-memory-mcp native executable or PyPI shim."""
        if self.executable_path:
            p = Path(self.executable_path)
            if p.exists() and p.is_file():
                return p

        # Check standard PATH
        which_path = shutil.which("codebase-memory-mcp") or shutil.which("codebase-memory-mcp.exe")
        if which_path:
            return Path(which_path)

        # Check default managed install locations
        candidates = [
            Path.home() / ".local" / "bin" / "codebase-memory-mcp",
            Path.home() / ".local" / "bin" / "codebase-memory-mcp.exe",
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                Path(local_app_data) / "Programs" / "codebase-memory-mcp" / "codebase-memory-mcp.exe"
            )
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    def check_health(self) -> ProviderResult:
        """Inspect CBM installation and readiness via --version."""
        exe = self._resolve_executable()
        if not exe:
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                diagnostic="codebase-memory-mcp executable not found in PATH or standard install dirs.",
            )

        t0 = time.monotonic()
        try:
            res = subprocess.run(
                [str(exe), "--version"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if res.returncode == 0:
                version = res.stdout.strip()
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    version=version,
                    capabilities=self.DEFAULT_TOOLS,
                    diagnostic=f"CBM CLI ready at {exe}",
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic=f"CBM --version failed (exit {res.returncode}): {res.stderr.strip()}",
                execution_time_ms=elapsed_ms,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                status=ProviderStatus.DEGRADED,
                provider=self.name(),
                diagnostic="CBM --version timed out after 1.5s.",
            )
        except Exception as exc:
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic=f"Failed to execute CBM binary: {exc}",
            )

    def is_available(self) -> bool:
        return self.check_health().is_ok()

    def run_tool(self, tool_name: str, args: dict[str, Any], timeout: float = 3.0) -> ProviderResult:
        """Execute a CBM tool via codebase-memory-mcp cli <tool> <args_json>."""
        exe = self._resolve_executable()
        if not exe:
            return self.check_health()

        t0 = time.monotonic()
        try:
            args_json = json.dumps(args)
            res = subprocess.run(
                [str(exe), "cli", tool_name, args_json],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if res.returncode == 0:
                out = res.stdout.strip()
                try:
                    parsed = json.loads(out)
                except Exception:
                    parsed = out
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    data=parsed,
                    diagnostic=f"Executed {tool_name} successfully.",
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic=f"CBM tool {tool_name} returned exit {res.returncode}: {res.stderr.strip()}",
                execution_time_ms=elapsed_ms,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                status=ProviderStatus.DEGRADED,
                provider=self.name(),
                diagnostic=f"CBM tool {tool_name} timed out after {timeout}s.",
            )
        except Exception as exc:
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic=f"CBM tool invocation failed: {exc}",
            )

    def get_symbol_map(self, repo_path: str) -> str:
        res = self.run_tool("search_graph", {"repo_path": repo_path, "limit": 50})
        if res.is_ok() and isinstance(res.data, dict):
            return json.dumps(res.data, indent=2)
        return ""

    def query_impact_result(self, query: str, paths: list[str]) -> ProviderResult:
        """Query code intelligence symbols and impact using CBM CLI."""
        exe = self._resolve_executable()
        if not exe:
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                data=[],
                diagnostic="codebase-memory-mcp binary not found; native code mapper used.",
            )

        # Try search_graph or detect_changes
        repo_target = paths[0] if paths else "."
        res = self.run_tool("search_graph", {"query": query, "repo_path": repo_target})
        if not res.is_ok():
            return res

        raw_data = res.data
        rows = []
        if isinstance(raw_data, list):
            rows = raw_data
        elif isinstance(raw_data, dict):
            rows = raw_data.get("results") or raw_data.get("nodes") or raw_data.get("items") or []

        mapped = []
        for r in rows:
            if isinstance(r, dict):
                mapped.append({
                    "symbol": r.get("name") or r.get("symbol") or "",
                    "path": r.get("path") or r.get("file") or "",
                    "details": r.get("details") or r.get("signature") or str(r),
                })

        if not mapped:
            return ProviderResult(
                status=ProviderStatus.NO_RESULTS,
                provider=self.name(),
                data=[],
                diagnostic=f"CBM found no structural symbols matching '{query}'.",
            )

        return ProviderResult(
            status=ProviderStatus.OK,
            provider=self.name(),
            data=mapped,
            diagnostic=f"Retrieved {len(mapped)} symbols from CBM.",
        )

