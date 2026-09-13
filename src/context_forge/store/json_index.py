from __future__ import annotations

import json
from pathlib import Path
from context_forge.store.paths import read_text, atomic_write
from context_forge.store.sqlite_index import tokenize, query_terms


def build_json_index(json_path: Path, docs: list[dict[str, str]]) -> None:
    inverted: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    for d in docs:
        titles[d["path"]] = d["title"]
        seen = set()
        for tok in tokenize(d["text"]):
            if tok in seen:
                continue
            seen.add(tok)
            inverted.setdefault(tok, []).append(d["path"])
    atomic_write(json_path, json.dumps({"titles": titles, "inverted": inverted}))


def search_json_index(json_path: Path, query: str, limit: int = 5) -> list[dict[str, str]]:
    terms = query_terms(query)
    if not terms or not json_path.exists():
        return []
    data = json.loads(read_text(json_path, "{}"))
    inverted = data.get("inverted", {})
    titles = data.get("titles", {})
    scores: dict[str, int] = {}
    for tok in terms:
        for path in inverted.get(tok, []):
            scores[path] = scores.get(path, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    return [{"path": path, "title": titles.get(path, path), "snippet": ""} for path, _ in ranked]
