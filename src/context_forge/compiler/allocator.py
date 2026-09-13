from __future__ import annotations

import copy
from typing import Any
from context_forge.core.budgets import Budgets


def _estimate_rendered_chars(task: str, sections: dict[str, list[Any]]) -> int:
    """Accurately estimate the character length of the rendered ContextPack text."""
    total = len(f"# Context Pack — Task: {task}\n") + 80  # Header and metadata

    # Provider diagnostics
    diags = sections.get("provider_diagnostics", [])
    if diags:
        total += len("## Provider Health & Intelligence Sources\n")
        for d in diags:
            msg = d.get("diagnostic_message") or d.get("diagnostic") if isinstance(d, dict) else str(d)
            total += len(f"- ℹ️ {msg}\n")
        total += 1

    # Authoritative intent
    intents = sections.get("authoritative_intent", [])
    if intents:
        total += len("## 1. Authoritative Intent (REQ / ADR)\n")
        for item in intents:
            total += len(f"- **[{item.get('id')}] {item.get('title')}** ({item.get('authority')})\n")
            if item.get("body"):
                total += len(f"  {item['body']}\n")
        total += 1

    # Current implementation
    impls = sections.get("current_implementation", [])
    if impls:
        total += len("## 2. Current Implementation Reality\n")
        for item in impls:
            total += len(f"- `{item.get('symbol') or item.get('path')}`: {item.get('details', '')}\n")
        total += 1

    # Past experience
    exps = sections.get("past_experience", [])
    if exps:
        total += len("## 3. Past Experience & Lessons\n")
        for item in exps:
            total += len(f"- {item.get('lesson') or item.get('finding')}\n")
        total += 1

    # Open questions
    qs = sections.get("open_questions", [])
    if qs:
        total += len("## 4. Open Uncertainties / Questions\n")
        for item in qs:
            total += len(f"- **[{item.get('id')}] {item.get('title')}**: {item.get('question')}\n")
        total += 1

    # Conflicts and staleness
    conflicts = sections.get("conflicts_and_staleness", [])
    if conflicts:
        total += len("## 5. Conflict & Staleness Alerts\n")
        for item in conflicts:
            total += len(f"- ⚠️ {item.get('warning')}\n")
        total += 1

    # Next reading
    readings = sections.get("next_reading", [])
    total += len("## 6. Next Reading (Recommended bounded files)\n")
    if readings:
        for f in readings:
            total += len(f"- `{f}`\n")
    else:
        total += len("- (No additional files required)\n")

    return total


def allocate_context_budget(
    pack_sections: dict[str, list[Any]],
    max_budget: int = Budgets.CONTEXT_PACK_CHARS,
    task: str = "",
) -> dict[str, list[Any]]:
    """Strictly enforce character budget using deterministic priority trimming.
    
    Priority order (highest to lowest):
      1. Critical conflicts / violations (DRIFT, VIOLATION)
      2. Authoritative intent / policy (REQ, ADR, POL)
      3. Open blocking questions
      4. Current implementation reality
      5. Relevant experiential memory
      6. Provider diagnostics
      7. Next-reading pointers
    """
    trimmed: dict[str, list[Any]] = {
        k: list(v) for k, v in pack_sections.items()
    }

    # Deterministic sorting on input items before trimming
    if "authoritative_intent" in trimmed:
        trimmed["authoritative_intent"] = sorted(
            trimmed["authoritative_intent"], key=lambda x: str(x.get("id", ""))
        )
    if "current_implementation" in trimmed:
        trimmed["current_implementation"] = sorted(
            trimmed["current_implementation"], key=lambda x: str(x.get("symbol") or x.get("path", ""))
        )
    if "past_experience" in trimmed:
        trimmed["past_experience"] = sorted(
            trimmed["past_experience"], key=lambda x: str(x.get("finding") or x.get("lesson", ""))
        )
    if "open_questions" in trimmed:
        trimmed["open_questions"] = sorted(
            trimmed["open_questions"], key=lambda x: str(x.get("id", ""))
        )
    if "conflicts_and_staleness" in trimmed:
        # DRIFT / VIOLATION sorted before STALE
        def conflict_key(c: dict[str, Any]) -> tuple[int, str]:
            t = str(c.get("type", "")).upper()
            rank = 0 if "DRIFT" in t or "VIOLATION" in t else 1
            return rank, str(c.get("warning", ""))
        trimmed["conflicts_and_staleness"] = sorted(
            trimmed["conflicts_and_staleness"], key=conflict_key
        )

    # Initial sane upper bounds
    trimmed["next_reading"] = trimmed.get("next_reading", [])[:Budgets.TURN_MAX_POINTERS + 2]
    trimmed["past_experience"] = trimmed.get("past_experience", [])[:5]
    trimmed["current_implementation"] = trimmed.get("current_implementation", [])[:8]
    trimmed["open_questions"] = trimmed.get("open_questions", [])[:4]
    trimmed["authoritative_intent"] = trimmed.get("authoritative_intent", [])[:6]

    # Iterative deterministic trimming loop to guarantee rendered_length <= max_budget
    while _estimate_rendered_chars(task, trimmed) > max_budget:
        # 1. Trim past_experience (lowest substantive priority)
        if len(trimmed.get("past_experience", [])) > 0:
            trimmed["past_experience"].pop()
            continue

        # 2. Trim next_reading pointers down to 1
        if len(trimmed.get("next_reading", [])) > 1:
            trimmed["next_reading"].pop()
            continue

        # 3. Trim current_implementation
        if len(trimmed.get("current_implementation", [])) > 1:
            trimmed["current_implementation"].pop()
            continue

        # 4. Trim open_questions
        if len(trimmed.get("open_questions", [])) > 1:
            trimmed["open_questions"].pop()
            continue

        # 5. Trim non-critical authoritative intent (keep at least 1)
        if len(trimmed.get("authoritative_intent", [])) > 1:
            trimmed["authoritative_intent"].pop()
            continue

        # 6. Truncate long bodies in authoritative intent
        truncated_any = False
        for item in trimmed.get("authoritative_intent", []):
            body = item.get("body", "")
            if len(body) > 120:
                item["body"] = body[:120] + "... [truncated for budget]"
                truncated_any = True
                break
        if truncated_any:
            continue

        # 7. Truncate current_implementation details
        for item in trimmed.get("current_implementation", []):
            det = item.get("details", "")
            if len(det) > 80:
                item["details"] = det[:80] + "..."
                truncated_any = True
                break
        if truncated_any:
            continue

        # If only critical conflict remains and still exceeds budget, preserve it (emergency overflow policy)
        break

    return trimmed
