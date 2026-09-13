from __future__ import annotations

import json
import re
import sys
from typing import Any
from context_forge.store.paths import STATE_DIR

AUTO_MANAGED_BRAIN_FILES = frozenset({"map.md", "index.md", "current-state.md", "log.md"})
PATCH_FILE_DIRECTIVE = re.compile(r"(?mi)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$")
PATCH_MOVE_DIRECTIVE = re.compile(r"(?mi)^\*\*\* Move to:\s*(.+?)\s*$")
PATH_TOKEN = re.compile(r"(?:(?:[A-Za-z]:)?[\\/])?[\w. -]+(?:[\\/][\w. -]+)*\.md", re.I)


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def emit_deny(event_name: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def tool_input_texts(tool_input: Any) -> list[str]:
    if isinstance(tool_input, str):
        return [tool_input]
    if not isinstance(tool_input, dict):
        return []

    texts = []
    for key in ("command", "patch", "file_path", "path", "input"):
        value = tool_input.get(key)
        if isinstance(value, str):
            texts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                texts.extend(tool_input_texts(edit))
    return texts


def normalize_path_components(path: str) -> list[str]:
    cleaned = str(path).strip().strip("'\"").strip(chr(96)).replace("\\", "/")
    return [part for part in cleaned.split("/") if part not in ("", ".")]


def is_auto_managed_brain_path(path: str) -> bool:
    components = normalize_path_components(path)
    lowered = [part.lower() for part in components]
    if ".brain" not in lowered:
        return False
    position = len(lowered) - 1 - lowered[::-1].index(".brain")
    tail = lowered[position + 1:]
    if not tail:
        return False
    return tail[0] == STATE_DIR or tail[-1] in AUTO_MANAGED_BRAIN_FILES


def patch_directive_paths(text: str) -> list[str]:
    return PATCH_FILE_DIRECTIVE.findall(text) + PATCH_MOVE_DIRECTIVE.findall(text)


def touched_paths_from_tool_input(tool_name: str, tool_input: Any) -> list[str]:
    paths = []
    for text in tool_input_texts(tool_input):
        directive_paths = patch_directive_paths(text)
        if directive_paths:
            paths.extend(directive_paths)
        else:
            paths.extend(PATH_TOKEN.findall(text))
    return list(dict.fromkeys(paths))


def has_parseable_patch(tool_input: Any) -> bool:
    return any(patch_directive_paths(text) for text in tool_input_texts(tool_input))


def handle_guard() -> None:
    payload = read_hook_input()
    event_name = payload.get("hook_event_name", "PreToolUse")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    supported = {"Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch", "Bash"}
    if tool_name not in supported:
        return

    texts = tool_input_texts(tool_input)
    if tool_name in {"Edit", "MultiEdit", "Write", "NotebookEdit"} and not texts:
        emit_deny(event_name, "context-forge could not inspect this memory-edit tool payload safely.")
        return
    if tool_name == "apply_patch" and not has_parseable_patch(tool_input):
        emit_deny(
            event_name,
            "context-forge could not safely parse this apply_patch payload. "
            "It was blocked rather than risking a write to protected memory files.",
        )
        return

    for path in touched_paths_from_tool_input(tool_name, tool_input):
        if is_auto_managed_brain_path(path):
            emit_deny(
                event_name,
                f"{path} is automatically managed context-forge state. "
                "Capture candidates must be reviewed and approved through brain.py; "
                "regenerate map.md through brain.py instead of editing it directly.",
            )
            return
