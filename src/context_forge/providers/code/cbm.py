from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional, Union
from context_forge.core.evidence import screen_secrets
from context_forge.providers.base import (
    CodeIntelligenceProvider,
    ProviderResult,
    ProviderStatus,
    VerificationKind,
    verification_confidence,
)


def normalize_repo_path(p: Path | str) -> str:
    """Normalize filesystem path for robust cross-platform CBM matching."""
    try:
        resolved = Path(p).resolve()
        norm = str(resolved).replace("\\", "/").rstrip("/")
        # On Windows, normalize casing for case-insensitive matching
        if os.name == "nt" or sys.platform == "win32":
            norm = norm.lower()
        return norm
    except (OSError, RuntimeError):
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

    def adapter_capabilities(self) -> list[str]:
        """Return list of capabilities/tools supported by this Context Forge adapter."""
        return list(self.DEFAULT_TOOLS)

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
            v_cmd = [str(exe), "--version"]
            if sys.platform == "win32" and str(exe).lower().endswith((".bat", ".cmd")):
                v_cmd = ["cmd.exe", "/c"] + v_cmd
            res = subprocess.run(
                v_cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if res.returncode == 0:
                version = res.stdout.strip()
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    version=version,
                    capabilities=[],  # Discovered capabilities; distinct from adapter-known tools
                    data={
                        "adapter_supported_capabilities": list(self.DEFAULT_TOOLS),
                        "discovered_capabilities": [],
                    },
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

    def run_tool(self, tool_name: str, args: dict[str, Any], timeout: float = 15.0) -> ProviderResult:
        """Execute a CBM tool via modern machine CLI flags (with fallback to inline JSON for legacy CBM) and secret screening."""
        exe = self._resolve_executable()
        if not exe:
            return self.check_health()

        t0 = time.monotonic()
        try:
            # Construct modern CLI command with machine-readable flags
            cmd = [str(exe), "cli", tool_name]
            for k, v in args.items():
                if v is None:
                    continue
                flag = "--" + k.replace("_", "-")
                if isinstance(v, bool):
                    if v:
                        cmd.append(flag)
                elif isinstance(v, (int, float, str)):
                    cmd.extend([flag, str(v)])
                elif isinstance(v, (list, dict)):
                    cmd.extend([flag, json.dumps(v)])

            if sys.platform == "win32" and str(exe).lower().endswith((".bat", ".cmd")):
                cmd = ["cmd.exe", "/c"] + cmd

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            # Compatibility fallback for older CBM binaries that only accept positional inline JSON
            if res.returncode != 0 and any(err_kw in res.stderr.lower() for err_kw in ("unrecognized argument", "unknown option", "unexpected argument")):
                args_json = json.dumps(args)
                fb_cmd = [str(exe), "cli", tool_name, args_json]
                if sys.platform == "win32" and str(exe).lower().endswith((".bat", ".cmd")):
                    fb_cmd = ["cmd.exe", "/c"] + fb_cmd
                fb_res = subprocess.run(
                    fb_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if fb_res.returncode == 0 or "unknown option" not in fb_res.stderr.lower():
                    res = fb_res
                    elapsed_ms = (time.monotonic() - t0) * 1000

            if res.returncode == 0:
                out = res.stdout.strip()
                try:
                    parsed = json.loads(out)
                except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
                    return ProviderResult(
                        status=ProviderStatus.MALFORMED,
                        provider=self.name(),
                        diagnostic_code="CBM_MALFORMED_JSON",
                        diagnostic=screen_secrets(f"CBM tool '{tool_name}' returned malformed JSON: {json_err} (raw: {out[:120]})"),
                        execution_time_ms=elapsed_ms,
                    )
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

    def resolve_project(
        self,
        repo_path: Path | str,
        allow_index: bool = True,
    ) -> tuple[Optional[str], Optional[ProviderResult]]:
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
        if self.auto_index and allow_index:
            idx_res = self.run_tool("index_repository", {"repo_path": str(Path(repo_path).resolve())}, timeout=30.0)
            if not idx_res.is_ok():
                return None, idx_res

            # Try to resolve project identity
            proj_name = None
            if isinstance(idx_res.data, dict):
                proj_name = idx_res.data.get("project") or idx_res.data.get("name") or idx_res.data.get("id")

            if not proj_name:
                list_res2 = self.run_tool("list_projects", {})
                if list_res2.is_ok() and isinstance(list_res2.data, (list, dict)):
                    projs2 = list_res2.data if isinstance(list_res2.data, list) else list_res2.data.get("projects", [])
                    for proj in projs2:
                        if isinstance(proj, dict):
                            p_path = proj.get("root_path") or proj.get("path") or ""
                            p_name = proj.get("name") or proj.get("project") or ""
                            if p_path and p_name and normalize_repo_path(p_path) == norm_target:
                                proj_name = p_name
                                break

            if proj_name:
                t_poll_start = time.monotonic()
                poll_timeout = 15.0
                while time.monotonic() - t_poll_start < poll_timeout:
                    status_res = self.run_tool("index_status", {"project": proj_name}, timeout=10.0)
                    if status_res.is_ok() and isinstance(status_res.data, dict):
                        st_val = str(status_res.data.get("status", "")).lower()
                        if st_val in ("ready", "indexed", "complete", "ok"):
                            self._project_cache[norm_target] = (proj_name, now)
                            return proj_name, None
                        elif st_val in ("indexing", "building", "in_progress", "pending"):
                            time.sleep(0.5)
                            continue
                        elif st_val in ("error", "failed"):
                            return None, ProviderResult(
                                status=ProviderStatus.ERROR,
                                provider=self.name(),
                                diagnostic_code="INDEX_STATUS_FAILED",
                                diagnostic=f"CBM index_status reported failed for project '{proj_name}': {status_res.data}",
                            )
                    elif status_res.status == ProviderStatus.ERROR and any(w in (status_res.diagnostic or "").lower() for w in ("not supported", "unknown tool")):
                        # index_status tool not supported on older CBM, assume completion
                        self._project_cache[norm_target] = (proj_name, now)
                        return proj_name, None
                    else:
                        return None, ProviderResult(
                            status=status_res.status,
                            provider=self.name(),
                            diagnostic_code=status_res.diagnostic_code or "INDEX_STATUS_UNEXPECTED",
                            diagnostic=status_res.diagnostic or f"CBM index_status returned unexpected response: {status_res.data}",
                        )

                # Polling timed out without reaching ready: NEVER cache, NEVER fail open
                return None, ProviderResult(
                    status=ProviderStatus.TIMEOUT,
                    provider=self.name(),
                    diagnostic_code="INDEXING_TIMEOUT",
                    diagnostic=f"CBM indexing for project '{proj_name}' did not reach READY within {poll_timeout}s.",
                )

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

    def query_impact_result(
        self,
        repo_path: Path | str,
        query: str = "",
        paths: Optional[list[str]] = None,
        scope_paths: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> ProviderResult:
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

        # Handle backward-compatible positional argument swap if query was passed as repo_path
        actual_repo = repo_path
        actual_query = query
        actual_scope = scope_paths if scope_paths is not None else (paths or [])

        # If repo_path is a query string without path separators and doesn't exist, and query is a list
        if isinstance(query, list) and not actual_scope:
            actual_scope = query
            actual_query = str(repo_path)
            actual_repo = kwargs.get("repo", ".")

        project_name, err = self.resolve_project(actual_repo)
        if err is not None:
            return err

        # Query search_graph using resolved project
        res = self.run_tool(
            "search_graph",
            {
                "project": project_name,
                "query": actual_query,
                "name_pattern": f".*{actual_query}.*" if actual_query else ".*",
                "limit": 40,
            },
        )
        if not res.is_ok():
            # Fallback to search_code
            res = self.run_tool(
                "search_code",
                {"project": project_name, "query": actual_query, "limit": 20},
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
                diagnostic=f"CBM found no structural symbols matching '{actual_query}' in project '{project_name}'.",
            )

        return ProviderResult(
            status=ProviderStatus.OK,
            provider=self.name(),
            data=mapped,
            project_name=project_name or "",
            diagnostic=f"Retrieved {len(mapped)} symbols from CBM project '{project_name}'.",
        )

    def verify_reference(
        self,
        repo_path: Path | str,
        path: str,
        symbol: Optional[str] = None,
        relationship: Optional[str] = None,
        target_symbol: Optional[str] = None,
        target_path: Optional[str] = None,
    ) -> ProviderResult:
        """Verify structural presence of a target file, symbol, or relationship using CBM CLI."""
        exe = self._resolve_executable()
        if not exe:
            return ProviderResult(
                status=ProviderStatus.UNAVAILABLE,
                provider=self.name(),
                diagnostic_code="CBM_UNAVAILABLE",
                diagnostic="codebase-memory-mcp binary not found.",
            )

        project_name, err = self.resolve_project(repo_path, allow_index=False)
        if err is not None:
            return err

        # Case 1: Relationship specified
        if relationship:
            query_target = symbol or path
            res = self.run_tool(
                "search_graph",
                {
                    "project": project_name,
                    "query": query_target,
                    "name_pattern": f"^{re.escape(symbol)}$" if symbol else ".*",
                    "limit": 20,
                },
            )
            if not res.is_ok():
                return res

            raw_data = res.data
            nodes = raw_data if isinstance(raw_data, list) else (raw_data.get("results") or raw_data.get("nodes") or []) if isinstance(raw_data, dict) else []
            rel_norm = relationship.lower().replace("-", "_")

            found_rel = False
            found_symbol = False
            matched_edge = None

            # Endpoint candidate sets
            src_tokens = {s.lower() for s in (symbol, path, Path(path).name, Path(path).stem) if s}
            tgt_tokens = set()
            if target_symbol or target_path:
                tgt_tokens = {t.lower() for t in (target_symbol, target_path, Path(target_path).name if target_path else None, Path(target_path).stem if target_path else None) if t}

            def _endpoint_matches(val: Any, tokens: set[str]) -> bool:
                if not val or not tokens:
                    return False
                if isinstance(val, dict):
                    extracted = [val.get("name"), val.get("symbol"), val.get("id"), val.get("path"), val.get("file")]
                    return any(str(e).lower() in tokens or any(t in str(e).lower() for t in tokens) for e in extracted if e)
                v_str = str(val).lower().replace("\\", "/")
                return any(t == v_str or v_str.endswith("/" + t) or t in v_str for t in tokens)

            if isinstance(raw_data, dict):
                edges = raw_data.get("edges") or raw_data.get("relationships") or []
                for edge in edges:
                    if isinstance(edge, dict):
                        e_type = str(edge.get("type") or edge.get("relationship") or edge.get("kind") or "").lower().replace("-", "_")
                        if e_type and (rel_norm in e_type or e_type in rel_norm):
                            e_src = edge.get("source") or edge.get("from") or edge.get("source_id") or edge.get("source_name") or edge.get("caller") or edge.get("origin")
                            e_tgt = edge.get("target") or edge.get("to") or edge.get("target_id") or edge.get("target_name") or edge.get("callee") or edge.get("destination")
                            
                            if tgt_tokens:
                                # Both endpoints must match: (src->tgt) or (tgt->src)
                                if (_endpoint_matches(e_src, src_tokens) and _endpoint_matches(e_tgt, tgt_tokens)) or \
                                   (_endpoint_matches(e_tgt, src_tokens) and _endpoint_matches(e_src, tgt_tokens)):
                                    found_rel = True
                                    matched_edge = edge
                                    break
                            else:
                                # At least one endpoint must connect to the query source (symbol or path)
                                if _endpoint_matches(e_src, src_tokens) or _endpoint_matches(e_tgt, src_tokens):
                                    found_rel = True
                                    matched_edge = edge
                                    break

            for node in nodes:
                if isinstance(node, dict):
                    name = node.get("name") or node.get("symbol") or ""
                    if symbol and name == symbol:
                        found_symbol = True
                    # Only verify relationship through node if this node matches the source
                    node_matches_src = _endpoint_matches(name, src_tokens) or _endpoint_matches(node.get("path"), src_tokens)
                    if node_matches_src:
                        n_rel = str(node.get("relationship") or node.get("relations") or "").lower().replace("-", "_")
                        if n_rel and (rel_norm in n_rel or n_rel in rel_norm):
                            if tgt_tokens:
                                n_target = node.get("target") or node.get("target_symbol") or node.get("target_name")
                                if _endpoint_matches(n_target, tgt_tokens):
                                    found_rel = True
                                    break
                            else:
                                found_rel = True
                                break

            # If target endpoint was specified and not found in search_graph, try trace_path tool
            if not found_rel and (target_symbol or target_path):
                tp_res = self.run_tool(
                    "trace_path",
                    {
                        "project": project_name,
                        "from": symbol or path,
                        "to": target_symbol or target_path,
                        "relationship": relationship,
                    },
                    timeout=10.0,
                )
                if tp_res.is_ok() and tp_res.data:
                    tp_data = tp_res.data
                    paths_found = tp_data.get("paths") or tp_data.get("path") if isinstance(tp_data, dict) else tp_data
                    if paths_found:
                        found_rel = True

            if found_rel:
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.STRUCTURAL_GRAPH.value,
                        "reference_verified": True,
                        "symbol_verified": True if symbol else False,
                        "relationship_requested": True,
                        "relationship_verified": True,
                        "structurally_verified": True,
                        "confidence": verification_confidence(VerificationKind.STRUCTURAL_GRAPH),
                        "reference_confidence": 0.95,
                        "relationship_confidence": 0.95,
                        "edge_confidence": 0.95,
                        "path": path,
                        "symbol": symbol,
                        "relationship": relationship,
                        "target_symbol": target_symbol,
                        "target_path": target_path,
                        "nodes": nodes,
                        "matched_edge": matched_edge,
                    },
                    diagnostic=f"Structural relationship '{relationship}' verified in CBM project '{project_name}'.",
                )
            elif found_symbol or nodes:
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.SYMBOL_GRAPH.value,
                        "reference_verified": True,
                        "symbol_verified": True,
                        "relationship_requested": True,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.SYMBOL_GRAPH),
                        "reference_confidence": 0.90,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.50,
                        "path": path,
                        "symbol": symbol,
                        "relationship": relationship,
                        "nodes": nodes,
                    },
                    diagnostic=f"Symbol '{query_target}' found in CBM graph, but relationship '{relationship}' was not structurally verified.",
                )
            else:
                # Check filesystem existence inside repo
                p_disk = Path(repo_path) / path
                if p_disk.exists():
                    return ProviderResult(
                        status=ProviderStatus.OK,
                        provider=self.name(),
                        project_name=project_name,
                        data={
                            "verification_kind": VerificationKind.FILESYSTEM.value,
                            "reference_verified": True,
                            "symbol_verified": False,
                            "relationship_requested": True,
                            "relationship_verified": False,
                            "structurally_verified": False,
                            "confidence": verification_confidence(VerificationKind.FILESYSTEM),
                            "reference_confidence": 0.50,
                            "relationship_confidence": 0.0,
                            "edge_confidence": 0.0,
                            "path": path,
                            "symbol": symbol,
                            "relationship": relationship,
                        },
                        diagnostic=f"Path '{path}' exists on disk; relationship '{relationship}' unverified by CBM.",
                    )
                return ProviderResult(
                    status=ProviderStatus.NO_RESULTS,
                    provider=self.name(),
                    project_name=project_name,
                    diagnostic=f"Reference '{query_target}' and relationship '{relationship}' not found in CBM project '{project_name}'.",
                )

        # Case 2: Symbol specified (without relationship)
        if symbol:
            res = self.run_tool(
                "search_graph",
                {
                    "project": project_name,
                    "query": symbol,
                    "name_pattern": f"^{re.escape(symbol)}$",
                    "limit": 10,
                },
            )
            if not res.is_ok():
                res = self.run_tool(
                    "search_code",
                    {"project": project_name, "query": symbol, "limit": 10},
                )
            if not res.is_ok():
                return res

            raw_data = res.data
            nodes = raw_data if isinstance(raw_data, list) else (raw_data.get("results") or raw_data.get("nodes") or []) if isinstance(raw_data, dict) else []
            if nodes:
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.SYMBOL_GRAPH.value,
                        "reference_verified": True,
                        "symbol_verified": True,
                        "relationship_requested": False,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.SYMBOL_GRAPH),
                        "reference_confidence": 0.90,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.90,
                        "symbol": symbol,
                        "path": path,
                        "nodes": nodes,
                    },
                    diagnostic=f"Symbol '{symbol}' verified in CBM project '{project_name}'.",
                )

            # Fallback to search_code or filesystem
            norm_path = path.replace("\\", "/")
            code_res = self.run_tool(
                "search_code",
                {"project": project_name, "query": norm_path or symbol, "limit": 5},
            )
            if code_res.is_ok() and code_res.data:
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.TEXT_SEARCH.value,
                        "reference_verified": True,
                        "symbol_verified": True,
                        "relationship_requested": False,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.TEXT_SEARCH),
                        "reference_confidence": 0.70,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.70,
                        "symbol": symbol,
                        "path": path,
                        "matches": code_res.data,
                    },
                    diagnostic=f"Symbol '{symbol}' matched via lexical code search in CBM project '{project_name}'.",
                )

            p_disk = Path(repo_path) / path
            if path and p_disk.exists():
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.FILESYSTEM.value,
                        "reference_verified": True,
                        "symbol_verified": False,
                        "relationship_requested": False,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.FILESYSTEM),
                        "reference_confidence": 0.50,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.50,
                        "symbol": symbol,
                        "path": path,
                    },
                    diagnostic=f"Path '{path}' confirmed on disk; symbol '{symbol}' not found in CBM graph.",
                )

            return ProviderResult(
                status=ProviderStatus.NO_RESULTS,
                provider=self.name(),
                project_name=project_name,
                diagnostic=f"Symbol '{symbol}' not found in CBM project '{project_name}'.",
            )

        # Case 3: Only path specified (neither symbol nor relationship)
        if path:
            norm_path = path.replace("\\", "/")
            res = self.run_tool(
                "search_code",
                {"project": project_name, "query": norm_path, "limit": 10},
            )
            if res.is_ok() and res.data:
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.TEXT_SEARCH.value,
                        "reference_verified": True,
                        "symbol_verified": False,
                        "relationship_requested": False,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.TEXT_SEARCH),
                        "reference_confidence": 0.70,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.70,
                        "path": path,
                        "matches": res.data,
                    },
                    diagnostic=f"Path '{path}' verified in CBM project '{project_name}'.",
                )
            p = Path(repo_path) / path
            if p.exists():
                return ProviderResult(
                    status=ProviderStatus.OK,
                    provider=self.name(),
                    project_name=project_name,
                    data={
                        "verification_kind": VerificationKind.FILESYSTEM.value,
                        "reference_verified": True,
                        "symbol_verified": False,
                        "relationship_requested": False,
                        "relationship_verified": False,
                        "structurally_verified": False,
                        "confidence": verification_confidence(VerificationKind.FILESYSTEM),
                        "reference_confidence": 0.50,
                        "relationship_confidence": 0.0,
                        "edge_confidence": 0.50,
                        "path": path,
                    },
                    diagnostic=f"Path '{path}' confirmed on disk in repository.",
                )
            return ProviderResult(
                status=ProviderStatus.NO_RESULTS,
                provider=self.name(),
                project_name=project_name,
                diagnostic=f"Reference '{path}' not found in CBM graph or repository.",
            )

        return ProviderResult(
            status=ProviderStatus.NO_RESULTS,
            provider=self.name(),
            project_name=project_name,
            diagnostic="Neither symbol nor path provided for reference verification.",
        )


CBMProvider = CodebaseMemoryMCPProvider

