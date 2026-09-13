from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from context_forge.store.paths import brain_paths, read_text


def resolve_traceability_graph(repo: Path) -> list[dict[str, Any]]:
    """Traverse REQ, ADR, TRACE, and TECH records to construct the living traceability graph."""
    p = brain_paths(repo)
    graph = []

    if not p["traceability"].exists():
        return graph

    for page in p["traceability"].glob("*.md"):
        text = read_text(page)
        source_m = re.search(r"## Source record\s*\n+- `([^`]+)`", text)
        paths = re.findall(r"## Related paths\s*\n+((?:- `[^`]+`\s*\n*)+)", text)

        source_id = source_m.group(1) if source_m else ""
        extracted_paths = []
        if paths:
            extracted_paths = re.findall(r"- `([^`]+)`", paths[0])

        tests = [p for p in extracted_paths if "test" in p.lower()]
        code_files = [p for p in extracted_paths if "test" not in p.lower()]

        graph.append({
            "trace_id": page.stem,
            "source_id": source_id,
            "code_files": code_files,
            "tests": tests,
        })
    return graph
