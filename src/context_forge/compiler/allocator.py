from __future__ import annotations

import copy
from typing import Any
from context_forge.core.budgets import Budgets


def _render_pack_text(task: str, sections: dict[str, list[Any]]) -> str:
    """Render ContextPack text format for exact character length measurement."""
    lines = [
        f"# Context Pack — Task: {task}",
        "_Compiled at 2026-09-14T00:00:00Z · Total chars: 0000 (~000 tokens)_",
        "",
    ]
    diags = sections.get("provider_diagnostics", [])
    if diags:
        lines.append("## Provider Health & Intelligence Sources")
        for d in diags:
            msg = d.get("diagnostic_message") or d.get("diagnostic") if isinstance(d, dict) else str(d)
            lines.append(f"- ℹ️ {msg}")
        lines.append("")

    intents = sections.get("authoritative_intent", [])
    if intents:
        lines.append("## 1. Authoritative Intent (REQ / ADR)")
        for item in intents:
            lines.append(f"- **[{item.get('id')}] {item.get('title')}** ({item.get('authority')})")
            if item.get("body"):
                lines.append(f"  {item['body']}")
        lines.append("")

    impls = sections.get("current_implementation", [])
    if impls:
        lines.append("## 2. Current Implementation Reality")
        for item in impls:
            lines.append(f"- `{item.get('symbol') or item.get('path')}`: {item.get('details', '')}")
        lines.append("")

    exps = sections.get("past_experience", [])
    if exps:
        lines.append("## 3. Past Experience & Lessons")
        for item in exps:
            lines.append(f"- {item.get('lesson') or item.get('finding')}")
        lines.append("")

    qs = sections.get("open_questions", [])
    if qs:
        lines.append("## 4. Open Uncertainties / Questions")
        for item in qs:
            lines.append(f"- **[{item.get('id')}] {item.get('title')}**: {item.get('question')}")
        lines.append("")

    conflicts = sections.get("conflicts_and_staleness", [])
    if conflicts:
        lines.append("## 5. Conflict & Staleness Alerts")
        for item in conflicts:
            lines.append(f"- ⚠️ {item.get('warning')}")
        lines.append("")

    readings = sections.get("next_reading", [])
    lines.append("## 6. Next Reading (Recommended bounded files)")
    if readings:
        for f in readings:
            lines.append(f"- `{f}`")
    else:
        lines.append("- (No additional files required)")
    lines.append("")

    return "\n".join(lines)


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
      6. Compact provider diagnostics
      7. Next-reading pointers
    """
    trimmed: dict[str, list[Any]] = {
        k: copy.deepcopy(v) for k, v in pack_sections.items()
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

    # Deterministic trimming loop: test exact rendered length <= max_budget
    while len(_render_pack_text(task, trimmed)) > max_budget:
        # Phase 1: Compact provider diagnostics if verbose
        compacted_diags = False
        for d in trimmed.get("provider_diagnostics", []):
            if isinstance(d, dict):
                msg = d.get("diagnostic_message") or d.get("diagnostic", "")
                if len(msg) > 70:
                    d["diagnostic_message"] = msg[:67] + "..."
                    d["diagnostic"] = d["diagnostic_message"]
                    compacted_diags = True
                    break
        if compacted_diags:
            continue

        # Phase 2: Trim past_experience (lowest substantive priority)
        if len(trimmed.get("past_experience", [])) > 0:
            trimmed["past_experience"].pop()
            continue

        # Phase 3: Trim next_reading pointers down to 1
        if len(trimmed.get("next_reading", [])) > 1:
            trimmed["next_reading"].pop()
            continue

        # Phase 4: Trim current_implementation
        if len(trimmed.get("current_implementation", [])) > 1:
            trimmed["current_implementation"].pop()
            continue

        # Phase 5: Trim open_questions
        if len(trimmed.get("open_questions", [])) > 1:
            trimmed["open_questions"].pop()
            continue

        # Phase 6: Compact provider diagnostics down to single line summary per provider
        if any(len(d.get("diagnostic_message", "")) > 40 for d in trimmed.get("provider_diagnostics", []) if isinstance(d, dict)):
            for d in trimmed.get("provider_diagnostics", []):
                if isinstance(d, dict):
                    prov = d.get("provider") or d.get("provider_name") or "provider"
                    stat = d.get("status", "ok")
                    code = d.get("diagnostic_code") or ""
                    d["diagnostic_message"] = f"{prov}: {stat}" + (f" ({code})" if code else "")
                    d["diagnostic"] = d["diagnostic_message"]
            continue

        # Phase 7: Trim non-critical authoritative intent (keep at least 1)
        if len(trimmed.get("authoritative_intent", [])) > 1:
            trimmed["authoritative_intent"].pop()
            continue

        # Phase 8: Truncate long bodies in authoritative intent
        truncated_intent = False
        for item in trimmed.get("authoritative_intent", []):
            body = item.get("body", "")
            if len(body) > 80:
                item["body"] = body[:77] + "..."
                truncated_intent = True
                break
        if truncated_intent:
            continue

        # Phase 9: Truncate current_implementation details
        truncated_impl = False
        for item in trimmed.get("current_implementation", []):
            det = item.get("details", "")
            if len(det) > 50:
                item["details"] = det[:47] + "..."
                truncated_impl = True
                break
        if truncated_impl:
            continue

        # Phase 10: Trim next_reading down to 0
        if len(trimmed.get("next_reading", [])) > 0:
            trimmed["next_reading"].pop()
            continue

        # Phase 11: Trim current_implementation down to 0
        if len(trimmed.get("current_implementation", [])) > 0:
            trimmed["current_implementation"].pop()
            continue

        # Phase 12: Trim open_questions down to 0
        if len(trimmed.get("open_questions", [])) > 0:
            trimmed["open_questions"].pop()
            continue

        # Phase 13: Trim provider diagnostics to 1 or 0
        if len(trimmed.get("provider_diagnostics", [])) > 0:
            trimmed["provider_diagnostics"].pop()
            continue

        # If only critical DRIFT / VIOLATION conflict and 1 intent remain and still exceed budget,
        # preserve them (explicitly documented unavoidable emergency overflow)
        break

    return trimmed
