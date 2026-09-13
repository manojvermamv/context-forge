from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from context_forge.core.models import CandidateRecord, IdentityEnvelope, now_iso
from context_forge.core.evidence import screen_secrets
from context_forge.store.paths import read_json, atomic_write

DECISION_HINTS = re.compile(r"\b(decided|decision|going with|we will|chose|instead of|switched to)\b", re.I)
BLOCKER_HINTS = re.compile(r"\b(blocked|blocker|TODO|FIXME|open question|not sure|unclear)\b", re.I)
NEXT_STEP_HINTS = re.compile(r"\b(next step|next up|still need to|will add|plan to|should add|remaining)\b", re.I)


def deterministic_delta(last_message: str) -> dict[str, list[str]]:
    """Extract decisions, blockers, and next steps deterministically without an LLM call."""
    decisions, blockers, next_steps = [], [], []
    for sent in re.split(r"(?<=[.!?])\s+", last_message or ""):
        sent = sent.strip()
        if not sent:
            continue
        if DECISION_HINTS.search(sent):
            decisions.append(sent[:200])
        elif BLOCKER_HINTS.search(sent):
            blockers.append(sent[:200])
        elif NEXT_STEP_HINTS.search(sent):
            next_steps.append(sent[:200])
    return {"decisions": decisions[:5], "blockers": blockers[:5], "next_steps": next_steps[:5]}


def sanitize_capture_delta(delta: dict[str, Any]) -> tuple[dict[str, list[str]], bool]:
    clean: dict[str, list[str]] = {"decisions": [], "blockers": [], "next_steps": []}
    contains_redactions = False
    if not isinstance(delta, dict):
        return clean, contains_redactions
    for key in clean:
        values = delta.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values[:5]:
            if not isinstance(value, str):
                continue
            item = value.strip()[:200]
            if not item:
                continue
            screened = screen_secrets(item)
            contains_redactions |= (screened != item)
            clean[key].append(screened)
    return clean, contains_redactions


def has_capture_content(delta: dict[str, list[str]]) -> bool:
    return any(delta.get(key) for key in ("decisions", "blockers", "next_steps"))


def compute_candidate_id(session: str, delta: dict[str, Any]) -> str:
    material = json.dumps({"session": session, "delta": delta}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def candidate_file(directory: Path, candidate_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{20}", candidate_id or ""):
        raise ValueError("invalid candidate id")
    return directory / f"{candidate_id}.json"


def stage_candidate(
    p: dict[str, Path],
    event: str,
    session: str,
    delta: dict[str, Any],
    redacted: bool,
    identity: IdentityEnvelope | None = None,
) -> str:
    """Stage a candidate in .brain/.state/pending/. Does NOT mutate canonical memory."""
    cid = compute_candidate_id(session, delta)
    p["pending"].mkdir(parents=True, exist_ok=True)
    target = candidate_file(p["pending"], cid)

    if target.exists():
        existing = read_json(target, {})
        events = existing.get("events", []) if isinstance(existing, dict) else []
        if event not in events:
            existing["events"] = events + [event]
            atomic_write(target, json.dumps(existing, indent=2, sort_keys=True) + "\n")
        return cid

    record = CandidateRecord(
        id=cid,
        status="pending",
        captured_at=now_iso(),
        events=[event],
        session=session,
        source="deterministic",
        contains_redactions=redacted,
        delta=delta,
        identity=identity or IdentityEnvelope(),
    )
    atomic_write(target, json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
    return cid


def pending_candidates(p: dict[str, Path]) -> list[dict[str, Any]]:
    if not p["pending"].exists():
        return []
    candidates = []
    for path in sorted(p["pending"].glob("*.json")):
        cand = read_json(path, {})
        if isinstance(cand, dict) and cand.get("status") == "pending":
            candidates.append(cand)
    return candidates
