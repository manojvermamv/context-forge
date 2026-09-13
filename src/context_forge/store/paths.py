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
    except Exception:
        return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
