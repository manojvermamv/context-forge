from __future__ import annotations

from pathlib import Path
from context_forge.store.paths import brain_paths
from context_forge.store.sqlite_index import search_fts5
from context_forge.store.json_index import search_json_index


def search_knowledge_index(repo: Path, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search knowledge records in .brain/ using SQLite FTS5 or fallback JSON index."""
    p = brain_paths(repo)
    if p["search_index_db"].exists():
        return search_fts5(p["search_index_db"], query, limit=limit)
    elif p["search_index_json"].exists():
        return search_json_index(p["search_index_json"], query, limit=limit)
    return []
