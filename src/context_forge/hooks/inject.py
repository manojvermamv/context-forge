from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from context_forge.core.budgets import Budgets
from context_forge.core.models import now_iso
from context_forge.store.paths import find_repo_root, brain_paths, read_text, atomic_write, read_json
from context_forge.store.sqlite_index import search_fts5
from context_forge.store.json_index import search_json_index
from context_forge.hooks.guard import read_hook_input

MEMORY_BLOCK_BEGIN = "<!-- context-forge:begin -->"
MEMORY_BLOCK_END = "<!-- context-forge:end -->"


def wrap_injected_memory(text: str) -> str:
    return f"{MEMORY_BLOCK_BEGIN}\n{text.strip()}\n{MEMORY_BLOCK_END}"


def emit_context(event_name: str, text: str) -> None:
    if not text:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def extract_session_identity(payload: dict, repo: Path) -> str:
    for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"session:{value.strip()}"
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        return "transcript:" + hashlib.sha256(transcript_path.encode()).hexdigest()[:16]
    return f"fallback:{repo.resolve()}:{now_iso()[:16]}"


def injection_marker(p: dict, payload: dict, repo: Path) -> Path:
    identity = extract_session_identity(payload, repo)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return p["injections"] / f"cold-start-{digest}.json"


def mark_cold_start_injection(p: dict, payload: dict, repo: Path) -> None:
    try:
        marker = injection_marker(p, payload, repo)
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(marker, json.dumps({"at": now_iso(), "consumed": False}) + "\n")
    except OSError:
        pass


def consume_cold_start_suppression(p: dict, payload: dict, repo: Path) -> bool:
    try:
        marker = injection_marker(p, payload, repo)
        record = read_json(marker, {})
        if not isinstance(record, dict) or record.get("consumed"):
            return False
        created = datetime.strptime(record.get("at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(seconds=Budgets.INJECTION_SUPPRESSION_SECONDS):
            return False
        record["consumed"] = True
        atomic_write(marker, json.dumps(record) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def extract_index_table(index_md: str, max_rows: int = 8) -> str:
    lines = [l for l in index_md.splitlines() if l.strip().startswith("- ")]
    return "\n".join(lines[:max_rows])


def handle_session_start() -> None:
    payload = read_hook_input()
    cwd = Path(payload.get("cwd") or os.getcwd())
    repo = find_repo_root(cwd)
    p = brain_paths(repo)
    event_name = payload.get("hook_event_name", "SessionStart")

    if not p["root"].exists():
        return

    source = str(payload.get("source", "startup"))
    compact_recovery = source == "compact" or event_name == "PostCompact"
    current_state = read_text(p["current_state"])
    index_md = read_text(p["index_md"])

    parts = [f"# context-forge — cold-start context ({source})", ""]
    if current_state:
        parts.append(current_state.strip())
    else:
        parts.append("_(no current-state.md yet — run `brain.py init`; inspect and approve a staged capture candidate)_")

    if not compact_recovery:
        route = extract_index_table(index_md, max_rows=8)
        if route:
            parts.append("\n## Wiki pages available (open only what you need)\n")
            parts.append(route)

    text = "\n".join(parts).strip()
    if len(text) > Budgets.L0_CHARS * 2:
        text = text[:Budgets.L0_CHARS * 2] + "\n\n_(truncated at hard ceiling — run `brain.py lint`)_"

    mark_cold_start_injection(p, payload, repo)
    emit_context(event_name, wrap_injected_memory(text))


def handle_turn_inject() -> None:
    payload = read_hook_input()
    cwd = Path(payload.get("cwd") or os.getcwd())
    repo = find_repo_root(cwd)
    p = brain_paths(repo)
    event_name = payload.get("hook_event_name", "UserPromptSubmit")
    prompt = payload.get("prompt", "")

    if not p["root"].exists() or not prompt.strip():
        return
    if consume_cold_start_suppression(p, payload, repo):
        return

    hits = []
    if p["search_index_db"].exists():
        hits = search_fts5(p["search_index_db"], prompt, limit=Budgets.TURN_MAX_POINTERS)
    elif p["search_index_json"].exists():
        hits = search_json_index(p["search_index_json"], prompt, limit=Budgets.TURN_MAX_POINTERS)

    if not hits:
        return  # Silent if no confident match

    lines = ["Related context-forge pages (open only if relevant):"]
    for h in hits:
        lines.append(f"- {h['title']} → `{h['path']}`")
    emit_context(event_name, wrap_injected_memory("\n".join(lines)))
