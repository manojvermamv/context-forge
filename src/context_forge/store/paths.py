from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BRAIN_DIR = ".brain"
STATE_DIR = ".state"
BRAIN_SCHEMA_VERSION = "2.0.0"


def find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git`. Falls back to `start.resolve()`."""
    cur = start.resolve()
    for _ in range(50):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


def brain_paths(repo: Path) -> dict[str, Path]:
    b = repo / BRAIN_DIR
    return {
        "root": b,
        "current_state": b / "current-state.md",
        "map": b / "map.md",
        "index_md": b / "index.md",
        "overview": b / "overview.md",
        "status": b / "status.md",
        "log": b / "log.md",
        "decisions": b / "decisions",
        "concepts": b / "concepts",
        "requirements": b / "requirements",
        "technical": b / "technical",
        "policies": b / "policies",
        "traceability": b / "traceability",
        "questions": b / "questions",
        "audit": b / "audit",
        "registry": b / "registry.json",
        "state": b / STATE_DIR,
        "pending": b / STATE_DIR / "pending",
        "approved": b / STATE_DIR / "approved",
        "discarded": b / STATE_DIR / "discarded",
        "reviews": b / STATE_DIR / "reviews",
        "reports": b / STATE_DIR / "reports",
        "injections": b / STATE_DIR / "injections",
        "search_index_json": b / STATE_DIR / "index.json",
        "search_index_db": b / STATE_DIR / "index.sqlite",
        "config": b / "config.json",
    }


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return default


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(read_text(path, ""))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}_{os.urandom(4).hex()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def is_safe_repo_path(repo: Path, target_path: str | Path) -> bool:
    """Verify that target_path stays strictly within the repository boundary.
    
    Rejects:
    - Absolute paths (unless pointing inside repo)
    - Path traversal attempts ('..') escaping repository root
    - Symlinks pointing outside repository root
    """
    if not target_path:
        return False
    repo_resolved = repo.resolve()
    path_str = str(target_path).strip().strip("'\"`").replace("\\", "/")
    
    parts = [p for p in path_str.split("/") if p and p != "."]
    if ".." in parts:
        return False
        
    try:
        p = Path(path_str)
        if p.is_absolute():
            resolved_p = p.resolve()
            return repo_resolved in resolved_p.parents or resolved_p == repo_resolved
            
        resolved_full = (repo / path_str).resolve()
        return repo_resolved in resolved_full.parents or resolved_full == repo_resolved
    except (OSError, RuntimeError, ValueError):
        return False


def canonicalize_repo_path(repo: Path, target_path: str | Path) -> str | None:
    """Return normalized relative path if target_path is safe, or None if containment is breached."""
    if not is_safe_repo_path(repo, target_path):
        return None
    try:
        path_str = str(target_path).strip().strip("'\"`").replace("\\", "/")
        full = (repo / path_str).resolve()
        rel = full.relative_to(repo.resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return None
