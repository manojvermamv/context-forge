from __future__ import annotations

import copy
from typing import Any, Optional

from context_forge.core.budgets import Budgets, ContextBudgetError
from context_forge.core.models import ContextPack


def reach_pack_fixed_point(pack: ContextPack, max_iterations: int = 5) -> str:
    """Iteratively update total_chars and estimated_tokens until rendered length reaches a fixed point."""
    for _ in range(max_iterations):
        rendered = pack.to_text()
        actual_len = len(rendered)
        actual_tokens = Budgets.rough_tokens(actual_len)
        if pack.total_chars == actual_len and pack.estimated_tokens == actual_tokens:
            return rendered
        pack.total_chars = actual_len
        pack.estimated_tokens = actual_tokens
    rendered = pack.to_text()
    pack.total_chars = len(rendered)
    pack.estimated_tokens = Budgets.rough_tokens(pack.total_chars)
    return rendered


def compact_alert(alert: dict[str, Any], level: int = 1) -> bool:
    """Deterministically compact alert warning while strictly preserving:
    - type
    - canonical_id / id
    - affected path / symbol
    - reconciliation_required (if present)
    Returns True if compaction actually reduced/changed the alert.
    """
    cur_level = alert.get("_compact_level", 0)
    if cur_level >= level:
        return False

    atype = str(alert.get("type", "ALERT")).upper()
    cid = str(alert.get("canonical_id") or alert.get("id") or "").strip()
    path = str(alert.get("path") or alert.get("symbol") or "").strip()

    if "summary" not in alert or not alert["summary"]:
        raw = str(alert.get("warning", "")).strip()
        if "indicates: " in raw:
            summary = raw.split("indicates: ", 1)[1].strip()
        elif "—" in raw:
            summary = raw.split("—", 1)[1].strip()
        elif ":" in raw:
            summary = raw.split(":", 1)[1].strip()
        else:
            summary = raw
        alert["summary"] = summary
    else:
        summary = str(alert["summary"]).strip()

    target_part = f" @ {path}" if path else ""
    cid_part = f" {cid}" if cid else ""

    if level == 1:
        brief = summary[:80].strip()
        alert["warning"] = f"{atype}{cid_part}{target_part} — {brief}".strip()
    elif level == 2:
        brief = summary[:40].strip()
        alert["warning"] = f"{atype}{cid_part}{target_part} — {brief}".strip()
    elif level == 3:
        brief = summary[:20].strip()
        alert["warning"] = f"{atype}{cid_part}{target_part} — {brief}".strip()
    else:
        # Minimal irreducible alert representation
        alert["warning"] = f"{atype}{cid_part}{target_part}".strip()

    alert["_compact_level"] = level
    return True


def finalize_context_pack(pack: ContextPack, max_budget: Optional[int] = None) -> ContextPack:
    """Deterministically enforce strict budget postcondition on ContextPack.
    
    Production Invariants:
      len(pack.to_text()) <= pack.budget_chars
      pack.total_chars == len(pack.to_text())
    
    Raises ContextBudgetError if even the irreducible header + minimal alert
    exceeds max_budget.
    """
    target_budget = max_budget if max_budget is not None else pack.budget_chars
    pack.budget_chars = target_budget

    rendered = reach_pack_fixed_point(pack)
    if len(rendered) <= target_budget:
        return pack

    # Trimming/Compaction Loop
    while len(reach_pack_fixed_point(pack)) > target_budget:
        # Step 0: Pathological task string compaction if > 60 chars
        if len(pack.task) > 60:
            pack.task = pack.task[:57] + "..."
            continue

        # Step 1: Compact provider diagnostics if verbose
        compacted_diags = False
        for d in pack.provider_diagnostics:
            if isinstance(d, dict):
                msg = d.get("diagnostic_message") or d.get("diagnostic", "")
                if len(msg) > 70:
                    d["diagnostic_message"] = msg[:67] + "..."
                    d["diagnostic"] = d["diagnostic_message"]
                    compacted_diags = True
                    break
        if compacted_diags:
            continue

        # Step 2: Trim past_experience (lowest substantive priority)
        if pack.past_experience:
            pack.past_experience.pop()
            continue

        # Step 3: Trim next_reading pointers down to 1
        if len(pack.next_reading) > 1:
            pack.next_reading.pop()
            continue

        # Step 4: Trim current_implementation down to 1
        if len(pack.current_implementation) > 1:
            pack.current_implementation.pop()
            continue

        # Step 5: Trim open_questions down to 1
        if len(pack.open_questions) > 1:
            pack.open_questions.pop()
            continue

        # Step 6: Compact provider diagnostics down to single line summary per provider
        diag_summarized = False
        for d in pack.provider_diagnostics:
            if isinstance(d, dict) and len(d.get("diagnostic_message", "")) > 40:
                prov = d.get("provider") or d.get("provider_name") or "provider"
                stat = d.get("status", "ok")
                code = d.get("diagnostic_code") or ""
                d["diagnostic_message"] = f"{prov}: {stat}" + (f" ({code})" if code else "")
                d["diagnostic"] = d["diagnostic_message"]
                diag_summarized = True
        if diag_summarized:
            continue

        # Step 7: Trim non-critical authoritative intent down to 1
        if len(pack.authoritative_intent) > 1:
            pack.authoritative_intent.pop()
            continue

        # Step 8: Truncate long bodies in authoritative intent (max 80 chars)
        truncated_intent = False
        for item in pack.authoritative_intent:
            body = item.get("body", "")
            if len(body) > 80:
                item["body"] = body[:77] + "..."
                truncated_intent = True
                break
        if truncated_intent:
            continue

        # Step 9: Truncate current_implementation details
        truncated_impl = False
        for item in pack.current_implementation:
            det = item.get("details", "")
            if len(det) > 40:
                item["details"] = det[:37] + "..."
                truncated_impl = True
                break
        if truncated_impl:
            continue

        # Step 10: Trim next_reading down to 0
        if pack.next_reading:
            pack.next_reading.pop()
            continue

        # Step 11: Trim current_implementation down to 0
        if pack.current_implementation:
            pack.current_implementation.pop()
            continue

        # Step 12: Trim open_questions down to 0
        if pack.open_questions:
            pack.open_questions.pop()
            continue

        # Step 13: Trim provider diagnostics down to 0
        if pack.provider_diagnostics:
            pack.provider_diagnostics.pop()
            continue

        # Step 14: Trim non-critical alerts (STALE, ADVICE_REJECTED, UNRESOLVED)
        popped_non_crit = False
        for idx in range(len(pack.conflicts_and_staleness) - 1, -1, -1):
            t = str(pack.conflicts_and_staleness[idx].get("type", "")).upper()
            if "DRIFT" not in t and "VIOLATION" not in t:
                pack.conflicts_and_staleness.pop(idx)
                popped_non_crit = True
                break
        if popped_non_crit:
            continue

        # Step 15: Strip authoritative intent body completely (keep title only)
        stripped_body = False
        for item in pack.authoritative_intent:
            if item.get("body"):
                item["body"] = ""
                stripped_body = True
                break
        if stripped_body:
            continue

        # Step 16: If no critical alerts remain, drop authoritative intent
        if pack.authoritative_intent and not pack.conflicts_and_staleness:
            pack.authoritative_intent.pop()
            continue

        # Step 17: Compact critical alerts in progressive levels
        alert_compacted = False
        for level in (1, 2, 3, 4):
            for c in pack.conflicts_and_staleness:
                if compact_alert(c, level=level):
                    alert_compacted = True
            if alert_compacted:
                break
        if alert_compacted:
            continue

        # Step 18: If authoritative intent still present alongside critical alerts, drop intent
        if pack.authoritative_intent:
            pack.authoritative_intent.pop()
            continue

        # Step 19: If multiple critical alerts remain and still exceed budget, pop lower priority alerts
        if len(pack.conflicts_and_staleness) > 1:
            pack.conflicts_and_staleness.pop()
            continue

        # Minimal irreducible alert reached
        break

    final_rendered = reach_pack_fixed_point(pack)
    if len(final_rendered) > target_budget:
        raise ContextBudgetError(
            f"ContextPack cannot fit within budget {target_budget} chars. "
            f"Irreducible minimum is {len(final_rendered)} chars."
        )

    return pack


def allocate_context_budget(
    pack_sections: dict[str, list[Any]],
    max_budget: int = Budgets.CONTEXT_PACK_CHARS,
    task: str = "",
) -> dict[str, list[Any]]:
    """Strictly enforce character budget using deterministic priority trimming and compaction."""
    # Deterministic sorting on input items before trimming
    cloned_sections: dict[str, list[Any]] = {
        k: copy.deepcopy(v) for k, v in pack_sections.items()
    }

    if "authoritative_intent" in cloned_sections:
        cloned_sections["authoritative_intent"] = sorted(
            cloned_sections["authoritative_intent"], key=lambda x: str(x.get("id", ""))
        )
    if "current_implementation" in cloned_sections:
        cloned_sections["current_implementation"] = sorted(
            cloned_sections["current_implementation"], key=lambda x: str(x.get("symbol") or x.get("path", ""))
        )
    if "past_experience" in cloned_sections:
        cloned_sections["past_experience"] = sorted(
            cloned_sections["past_experience"], key=lambda x: str(x.get("finding") or x.get("lesson", ""))
        )
    if "open_questions" in cloned_sections:
        cloned_sections["open_questions"] = sorted(
            cloned_sections["open_questions"], key=lambda x: str(x.get("id", ""))
        )
    if "conflicts_and_staleness" in cloned_sections:
        def conflict_key(c: dict[str, Any]) -> tuple[int, str]:
            t = str(c.get("type", "")).upper()
            rank = 0 if "DRIFT" in t or "VIOLATION" in t else 1
            return rank, str(c.get("warning", ""))
        cloned_sections["conflicts_and_staleness"] = sorted(
            cloned_sections["conflicts_and_staleness"], key=conflict_key
        )

    # Initial sane upper bounds
    cloned_sections["next_reading"] = cloned_sections.get("next_reading", [])[:Budgets.TURN_MAX_POINTERS + 2]
    cloned_sections["past_experience"] = cloned_sections.get("past_experience", [])[:5]
    cloned_sections["current_implementation"] = cloned_sections.get("current_implementation", [])[:8]
    cloned_sections["open_questions"] = cloned_sections.get("open_questions", [])[:4]
    cloned_sections["authoritative_intent"] = cloned_sections.get("authoritative_intent", [])[:6]

    temp_pack = ContextPack(
        task=task,
        budget_chars=max_budget,
        authoritative_intent=cloned_sections.get("authoritative_intent", []),
        current_implementation=cloned_sections.get("current_implementation", []),
        past_experience=cloned_sections.get("past_experience", []),
        open_questions=cloned_sections.get("open_questions", []),
        conflicts_and_staleness=cloned_sections.get("conflicts_and_staleness", []),
        next_reading=cloned_sections.get("next_reading", []),
        provider_diagnostics=cloned_sections.get("provider_diagnostics", []),
    )

    try:
        finalize_context_pack(temp_pack, max_budget=max_budget)
    except ContextBudgetError:
        pass

    return {
        "authoritative_intent": temp_pack.authoritative_intent,
        "current_implementation": temp_pack.current_implementation,
        "past_experience": temp_pack.past_experience,
        "open_questions": temp_pack.open_questions,
        "conflicts_and_staleness": temp_pack.conflicts_and_staleness,
        "next_reading": temp_pack.next_reading,
        "provider_diagnostics": temp_pack.provider_diagnostics,
    }
