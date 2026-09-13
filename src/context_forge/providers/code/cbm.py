from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from context_forge.core.evidence import screen_secrets
from context_forge.providers.base import CodeIntelligenceProvider, ProviderResult, ProviderStatus


def normalize_repo_path(p: Path | str) -> str:
    """Normalize filesystem path for robust cross-platform CBM matching."""
    try:
        resolved = Path(p).resolve()
        norm = str(resolved).replace("\\", "/").rstrip("/")
        # On Windows, normalize casing for case-insensitive matching
        if os.name == "nt" or sys.platform == "win32":
            norm = norm.lower()
        return norm
    except Exception:
        return str(p).replace("\\", "/").rstrip("/").lower()


class CodebaseMemoryMCPProvider(CodeIntelligenceProvider):
    """Integrates with Codebase-Memory-MCP via its CLI surface (codebase-memory-mcp cli <tool> <json>)."""

    DEFAULT_TOOLS = [
        "list_projects",
        "index_repository",
        "search_graph",
        "trace_path",
        "detect_changes",
        "search_code",
        "get_architecture",
    ]

    def __init__(
        self,
        executable_path: str | None = None,
        endpoint_url: str | None = None,
        auto_index: bool | None = None,
        cache_ttl_seconds: float = 60.0,
    ):
        self.executable_path = (
            executable_path
            or os.environ.get("CBM_PATH")
            or os.environ.get("CODEBASE_MEMORY_PATH")
        )
        self.endpoint_url = endpoint_url or os.environ.get("CBM_HTTP_URL")
        self.auto_index = (
            auto_index
            if auto_index is not None
            else os.environ.get("CBM_AUTO_INDEX", "0").lower() in ("1", "true", "yes")
        )
        self.cache_ttl_seconds = cache_ttl_seconds
        # In-memory cache: normalized_path -> (project_name, timestamp)
        self._project_cache: dict[str, tuple[str, float]] = {}

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
        """Inspect CBM installation, version, and tool capabilities."""
        exe = self._resolve_executable()
        if not exe:
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                diagnostic_code="CBM_BINARY_NOT_FOUND",
                diagnostic="codebase-memory-mcp executable not found in PATH or standard install dirs.",
            )

        t0 = time.monotonic()
        try:
            res = subprocess.run(
                [str(exe), "--version"],
                capture_output=True,
                text=True,
                timeout=5.0,
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
                diagnostic_code="CBM_VERSION_ERROR",
                diagnostic=screen_secrets(f"CBM --version failed (exit {res.returncode}): {res.stderr.strip()}"),
                execution_time_ms=elapsed_ms,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                status=ProviderStatus.DEGRADED,
                provider=self.name(),
                diagnostic_code="CBM_TIMEOUT",
                diagnostic="CBM --version timed out after 5.0s.",
            )
        except Exception as exc:
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code="CBM_EXEC_EXCEPTION",
                diagnostic=screen_secrets(f"Failed to execute CBM binary: {exc}"),
            )

    def is_available(self) -> bool:
        return self.check_health().is_ok()

    def run_tool(self, tool_name: str, args: dict[str, Any], timeout: float = 6.0) -> ProviderResult:
        """Execute a CBM tool via codebase-memory-mcp cli <tool> <args_json> with secret screening."""
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
                    diagnostic=f"Executed tool '{tool_name}' successfully.",
                    execution_time_ms=elapsed_ms,
                )
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code=f"CBM_TOOL_EXIT_{res.returncode}",
                diagnostic=screen_secrets(f"CBM tool '{tool_name}' returned exit {res.returncode}: {res.stderr.strip()}"),
                execution_time_ms=elapsed_ms,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                status=ProviderStatus.TIMEOUT,
                provider=self.name(),
                diagnostic_code="CBM_TOOL_TIMEOUT",
                diagnostic=f"CBM tool '{tool_name}' timed out after {timeout}s.",
            )
        except Exception as exc:
            return ProviderResult(
                status=ProviderStatus.ERROR,
                provider=self.name(),
                diagnostic_code="CBM_TOOL_EXCEPTION",
                diagnostic=screen_secrets(f"CBM tool '{tool_name}' invocation failed: {exc}"),
            )

    def resolve_project(self, repo_path: Path | str) -> tuple[Optional[str], Optional[ProviderResult]]:
        """Resolve a local repository path to a CBM project identity via list_projects / index_repository."""
        norm_target = normalize_repo_path(repo_path)
        now = time.monotonic()

        # Check cache
        if norm_target in self._project_cache:
            name, cached_time = self._project_cache[norm_target]
            if now - cached_time < self.cache_ttl_seconds:
                return name, None

        # Query list_projects
        list_res = self.run_tool("list_projects", {})
        if not list_res.is_ok():
            return None, list_res

        projects = []
        raw_data = list_res.data
        if isinstance(raw_data, list):
            projects = raw_data
        elif isinstance(raw_data, dict):
            projects = raw_data.get("projects") or raw_data.get("results") or raw_data.get("items") or []

        for proj in projects:
            if not isinstance(proj, dict):
                continue
            proj_path = proj.get("root_path") or proj.get("path") or proj.get("dir") or ""
            proj_name = proj.get("name") or proj.get("project") or proj.get("id") or ""
            if proj_path and proj_name:
                norm_proj = normalize_repo_path(proj_path)
                if norm_proj == norm_target or norm_target.startswith(norm_proj + "/"):
                    self._project_cache[norm_target] = (proj_name, now)
                    return proj_name, None

        # Project not found in CBM
        if self.auto_index:
            idx_res = self.run_tool("index_repository", {"repo_path": str(Path(repo_path).resolve())}, timeout=15.0)
            if idx_res.is_ok():
                # Re-query list_projects once
                list_res2 = self.run_tool("list_projects", {})
                if list_res2.is_ok() and isinstance(list_res2.data, (list, dict)):
                    projs2 = list_res2.data if isinstance(list_res2.data, list) else list_res2.data.get("projects", [])
                    for proj in projs2:
                        if isinstance(proj, dict):
                            p_path = proj.get("root_path") or proj.get("path") or ""
                            p_name = proj.get("name") or proj.get("project") or ""
                            if p_path and p_name and normalize_repo_path(p_path) == norm_target:
                                self._project_cache[norm_target] = (p_name, now)
                                return p_name, None

        return None, ProviderResult(
            status=ProviderStatus.UNINDEXED,
            provider=self.name(),
            diagnostic_code="REPO_NOT_INDEXED",
            diagnostic=f"Repository '{repo_path}' is not indexed in CBM. Run 'codebase-memory-mcp cli index_repository' or enable auto_index.",
        )

    def get_symbol_map(self, repo_path: str) -> str:
        project_name, err = self.resolve_project(repo_path)
        if not project_name:
            return ""
        res = self.run_tool("search_graph", {"project": project_name, "limit": 50})
        if res.is_ok() and isinstance(res.data, (dict, list)):
            return json.dumps(res.data, indent=2)
        return ""

    def query_impact_result(self, query: str, paths: list[str]) -> ProviderResult:
        """Query code intelligence symbols and impact using CBM CLI with resolved project identity."""
        exe = self._resolve_executable()
        if not exe:
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                data=[],
                diagnostic_code="CBM_UNAVAILABLE",
                diagnostic="codebase-memory-mcp binary not found; native code mapper used.",
            )

        repo_target = paths[0] if paths else "."
        project_name, err = self.resolve_project(repo_target)
        if err is not None:
            return err

        # Query search_graph using resolved project
        res = self.run_tool(
            "search_graph",
            {
                "project": project_name,
                "query": query,
                "name_pattern": f".*{query}.*" if query else ".*",
                "limit": 40,
            },
        )
        if not res.is_ok():
            # Fallback to search_code
            res = self.run_tool(
                "search_code",
                {"project": project_name, "query": query, "limit": 20},
            )
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
                project_name=project_name or "",
                diagnostic=f"CBM found no structural symbols matching '{query}' in project '{project_name}'.",
            )

        return ProviderResult(
            status=ProviderStatus.OK,
            provider=self.name(),
            data=mapped,
            project_name=project_name or "",
            diagnostic=f"Retrieved {len(mapped)} symbols from CBM project '{project_name}'.",
        )


CBMProvider = CodebaseMemoryMCPProvider

