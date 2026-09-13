from __future__ import annotations

import os
from pathlib import Path
from context_forge.core.budgets import Budgets
from context_forge.core.identity import build_identity_envelope
from context_forge.store.paths import find_repo_root, brain_paths
from context_forge.knowledge.candidate import (
    deterministic_delta,
    sanitize_capture_delta,
    has_capture_content,
    stage_candidate,
)
from context_forge.hooks.guard import read_hook_input
from context_forge.hooks.inject import extract_session_identity, MEMORY_BLOCK_BEGIN, MEMORY_BLOCK_END

import re

INJECTED_MEMORY_BLOCK = re.compile(
    rf"(?is){re.escape(MEMORY_BLOCK_BEGIN)}.*?{re.escape(MEMORY_BLOCK_END)}"
)


def strip_injected_memory(text: str) -> str:
    return INJECTED_MEMORY_BLOCK.sub("", text or "")


def read_transcript_tail(value: str | None) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        path = Path(value)
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - Budgets.CAPTURE_TAIL_BYTES))
            return handle.read(Budgets.CAPTURE_TAIL_BYTES).decode("utf-8", errors="ignore")
    except (OSError, ValueError):
        return ""


def handle_capture(event: str) -> None:
    """Fast, fail-open hook path: stage only, never mutate canonical memory."""
    try:
        payload = read_hook_input()
        cwd = Path(payload.get("cwd") or os.getcwd())
        repo = find_repo_root(cwd)
        p = brain_paths(repo)
        if not p["root"].exists():
            return

        message = payload.get("last_assistant_message") or payload.get("lastAssistantMessage") or ""
        if not isinstance(message, str) or not message.strip():
            message = read_transcript_tail(payload.get("transcript_path"))

        message = strip_injected_memory(message)
        delta, redacted = sanitize_capture_delta(deterministic_delta(message))
        if not has_capture_content(delta):
            return

        identity = build_identity_envelope(payload, repo)
        stage_candidate(p, event, extract_session_identity(payload, repo), delta, redacted, identity=identity)
    except (OSError, TypeError, ValueError):
        return
