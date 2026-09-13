from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator
from context_forge.core.budgets import Budgets
from context_forge.core.models import now_iso
from context_forge.providers.base import CodeIntelligenceProvider

IGNORE_DIRS = {
    ".git", ".brain", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".turbo", "target", ".pytest_cache",
    ".mypy_cache", "vendor", ".idea", ".vscode", "coverage",
}

CODE_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".swift": "swift", ".scala": "scala", ".sh": "shell",
}

SYMBOL_PATTERNS = {
    "python": re.compile(r"^\s*(?:async\s+def|def|class)\s+(\w+)"),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"
        r"|^\s*(?:export\s+)?class\s+(\w+)"
        r"|^\s*export\s+const\s+(\w+)\s*="
    ),
    "typescript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"
        r"|^\s*(?:export\s+)?class\s+(\w+)"
        r"|^\s*(?:export\s+)?interface\s+(\w+)"
        r"|^\s*export\s+const\s+(\w+)\s*="
    ),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s+(?:struct|interface)"),
    "rust": re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)|^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"),
    "java": re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?class\s+(\w+)"),
}


def iter_source_files(repo: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            ext = Path(fn).suffix
            if ext in CODE_EXT_LANG:
                yield Path(dirpath) / fn


def detect_project_markers(repo: Path) -> list[str]:
    markers = {
        "package.json": "Node.js / JavaScript", "pyproject.toml": "Python (pyproject)",
        "setup.py": "Python (setuptools)", "requirements.txt": "Python (pip)",
        "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java (Maven)",
        "build.gradle": "Java/Kotlin (Gradle)", "Gemfile": "Ruby",
        "composer.json": "PHP", "*.csproj": "C#/.NET",
    }
    found = []
    for name, label in markers.items():
        if "*" in name:
            if list(repo.glob(name)):
                found.append(label)
        elif (repo / name).exists():
            found.append(label)
    return found


def build_code_map(repo: Path) -> str:
    files = list(iter_source_files(repo))
    by_lang: dict[str, int] = {}
    top_dirs: dict[str, int] = {}
    symbol_lines = []

    for f in files:
        rel = str(f.relative_to(repo))
        top = rel.split(os.sep, 1)[0]
        top_dirs[top] = top_dirs.get(top, 0) + 1
        lang = CODE_EXT_LANG.get(f.suffix, "other")
        by_lang[lang] = by_lang.get(lang, 0) + 1

        pat = SYMBOL_PATTERNS.get(lang)
        if pat is None:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = pat.match(line)
            if m:
                name = next((g for g in m.groups() if g), None)
                if name:
                    symbol_lines.append((rel, i, name))

    markers = detect_project_markers(repo)
    lines = [
        "# Code Map",
        "",
        f"_Generated deterministically (no LLM call) at {now_iso()}. Regenerate anytime with_",
        "`brain.py map <repo>`. _This replaces cold repo exploration — read this before Glob/Grep._",
        "",
        "## Project type",
        ", ".join(markers) if markers else "(no standard marker file detected)",
        "",
        f"## Scale — {len(files)} source files across {len(top_dirs)} top-level directories",
        "",
        "| Top-level dir | source files |",
        "|---|---|",
    ]
    for d, n in sorted(top_dirs.items(), key=lambda kv: -kv[1])[:20]:
        lines.append(f"| `{d}/` | {n} |")

    lines += ["", "## Languages", "", "| Language | files |", "|---|---|"]
    for lang, n in sorted(by_lang.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {lang} | {n} |")

    lines += ["", "## Key symbols (top-level def/class only, capped)", ""]
    budget_used = sum(len(l) + 1 for l in lines)
    per_file: dict[str, list[tuple[int, str]]] = {}
    for rel, ln, name in symbol_lines:
        per_file.setdefault(rel, []).append((ln, name))

    remaining = Budgets.MAP_CHARS - budget_used
    emitted_files = 0
    for rel, syms in sorted(per_file.items()):
        if remaining <= 0:
            lines.append(
                f"_...map budget ({Budgets.MAP_CHARS} chars) reached — "
                f"{len(per_file) - emitted_files} more files have symbols not shown; "
                "use `brain.py search <repo> <name>` instead._"
            )
            break
        row = f"- `{rel}`: " + ", ".join(f"{name}:{ln}" for ln, name in syms[:12])
        if len(syms) > 12:
            row += f" (+{len(syms) - 12} more)"
        lines.append(row)
        remaining -= len(row)
        emitted_files += 1

    return "\n".join(lines) + "\n"


from context_forge.providers.base import CodeIntelligenceProvider, ProviderResult, ProviderStatus


class NativeCodeProvider(CodeIntelligenceProvider):
    """Native code intelligence based on deterministic symbol scanning."""

    def name(self) -> str:
        return "native_code_map"

    def is_available(self) -> bool:
        return True

    def check_health(self) -> ProviderResult:
        return ProviderResult(
            status=ProviderStatus.OK,
            provider=self.name(),
            version="2.0",
            capabilities=["deterministic_symbol_map", "file_impact"],
            diagnostic="Native deterministic code mapper ready.",
        )

    def get_symbol_map(self, repo_path: str) -> str:
        return build_code_map(Path(repo_path))

    def query_impact_result(
        self,
        repo_path: Path | str,
        query: str = "",
        paths: Optional[list[str]] = None,
        scope_paths: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> ProviderResult:
        # Handle backward-compatible positional argument swap if query was passed as repo_path
        actual_scope = scope_paths if scope_paths is not None else (paths or [])
        if isinstance(query, list) and not actual_scope:
            actual_scope = query

        results = []
        for p in actual_scope:
            results.append({"path": p, "details": "Directly specified in task scope."})
        status = ProviderStatus.OK if results else ProviderStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.name(),
            data=results,
            diagnostic=f"Native scope mapping for {len(actual_scope)} paths.",
        )

    def verify_reference(
        self,
        repo_path: Path | str,
        path: str,
        symbol: Optional[str] = None,
        relationship: Optional[str] = None,
    ) -> ProviderResult:
        """Verify presence of file or symbol in repository using native scanning."""
        target = Path(repo_path) / path
        if not target.exists():
            return ProviderResult(
                status=ProviderStatus.NO_RESULTS,
                provider=self.name(),
                diagnostic=f"Native verification: file '{path}' not found on disk.",
            )

        if symbol:
            try:
                content = target.read_text(encoding="utf-8", errors="ignore")
                lang = CODE_EXT_LANG.get(target.suffix, "other")
                pat = SYMBOL_PATTERNS.get(lang)
                if pat:
                    for line in content.splitlines():
                        m = pat.match(line)
                        if m and any(g == symbol for g in m.groups() if g):
                            return ProviderResult(
                                status=ProviderStatus.OK,
                                provider=self.name(),
                                data={"path": path, "symbol": symbol},
                                diagnostic=f"Native verification: symbol '{symbol}' found in '{path}'.",
                            )
                if symbol in content:
                    return ProviderResult(
                        status=ProviderStatus.OK,
                        provider=self.name(),
                        data={"path": path, "symbol": symbol},
                        diagnostic=f"Native verification: symbol '{symbol}' matched in '{path}'.",
                    )
            except OSError:
                pass

            return ProviderResult(
                status=ProviderStatus.NO_RESULTS,
                provider=self.name(),
                diagnostic=f"Native verification: symbol '{symbol}' not found in '{path}'.",
            )

        return ProviderResult(
            status=ProviderStatus.OK,
            provider=self.name(),
            data={"path": path},
            diagnostic=f"Native verification: file '{path}' exists.",
        )

