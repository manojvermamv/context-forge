from __future__ import annotations

import json
from pathlib import Path
from context_forge.core.models import now_iso
from context_forge.store.paths import STATE_DIR, BRAIN_DIR, BRAIN_SCHEMA_VERSION, read_text, atomic_write
from context_forge.store.markdown import extract_frontmatter_dict, first_heading

ROUTE_BEGIN = "<!-- context-forge:auto-routes:begin -->"
ROUTE_END = "<!-- context-forge:auto-routes:end -->"

ROUTED_SECTIONS = (
    ("Decisions", "decisions"),
    ("Requirements", "requirements"),
    ("Technical knowledge", "technical"),
    ("Traceability", "traceability"),
    ("Open questions", "questions"),
    ("Concepts", "concepts"),
)


def record_kind(path: Path, root: Path) -> str:
    try:
        first = path.relative_to(root).parts[0]
    except ValueError:
        return "core"
    return {
        "decisions": "decision",
        "requirements": "requirement",
        "technical": "technical",
        "traceability": "traceability",
        "questions": "question",
        "concepts": "concept",
    }.get(first, "core")


def write_registry(repo: Path, p: dict) -> None:
    """Generate a small machine-readable catalog from the portable Markdown wiki."""
    records = []
    for page in sorted(p["root"].rglob("*.md")):
        if STATE_DIR in page.parts or (p["audit"].exists() and p["audit"] in page.parents):
            continue
        text = read_text(page)
        fm, _ = extract_frontmatter_dict(text)
        records.append({
            "path": page.relative_to(p["root"]).as_posix(),
            "id": fm.get("id", ""),
            "kind": record_kind(page, p["root"]),
            "title": first_heading(text) or page.stem,
            "status": fm.get("status", ""),
            "authority": fm.get("authority", ""),
            "updated": fm.get("updated") or fm.get("date", ""),
            "freshness": fm.get("freshness", "fresh"),
        })
    payload = {
        "schema_version": BRAIN_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "root": BRAIN_DIR,
        "records": records,
    }
    atomic_write(p["registry"], json.dumps(payload, indent=2, sort_keys=True) + "\n")


def sync_routing_index(p: dict) -> None:
    """Refresh only the generated on-demand routes in index.md between markers."""
    original = read_text(p["index_md"])
    if not original:
        return

    lines = [ROUTE_BEGIN]
    for label, key in ROUTED_SECTIONS:
        lines += [f"### {label} (`{key}/`)"]
        section = p[key]
        pages = sorted(section.rglob("*.md")) if section.exists() else []
        if pages:
            for page in pages:
                title = first_heading(read_text(page)) or page.stem
                title = title.replace("[", "\\[").replace("]", "\\]")
                lines.append(f"- [{title}]({page.relative_to(p['root']).as_posix()})")
        else:
            lines.append("_(none yet)_")
        lines.append("")
    lines.append(ROUTE_END)
    generated = "\n".join(lines)

    begin = original.find(ROUTE_BEGIN)
    end = original.find(ROUTE_END, begin + len(ROUTE_BEGIN)) if begin >= 0 else -1
    if begin >= 0 and end >= begin:
        updated = original[:begin] + generated + original[end + len(ROUTE_END):]
    else:
        updated = original.rstrip() + "\n\n## On-demand pages\n\n" + generated + "\n"
    if updated != original:
        atomic_write(p["index_md"], updated)
