from __future__ import annotations

from typing import Any
from context_forge.core.budgets import Budgets


def allocate_context_budget(
    pack_sections: dict[str, list[Any]],
    max_budget: int = Budgets.CONTEXT_PACK_CHARS,
) -> dict[str, list[Any]]:
    """Allocate and trim section items to ensure the total context pack stays within character budget."""
    # Priority order:
    # 1. Conflicts and staleness alerts (crucial)
    # 2. Authoritative intent (REQ/ADR)
    # 3. Open questions
    # 4. Current implementation (capped)
    # 5. Past experience (capped)
    # 6. Next reading
    trimmed = dict(pack_sections)
    trimmed["authoritative_intent"] = trimmed.get("authoritative_intent", [])[:4]
    trimmed["current_implementation"] = trimmed.get("current_implementation", [])[:5]
    trimmed["past_experience"] = trimmed.get("past_experience", [])[:3]
    trimmed["open_questions"] = trimmed.get("open_questions", [])[:3]
    trimmed["conflicts_and_staleness"] = trimmed.get("conflicts_and_staleness", [])[:3]
    trimmed["next_reading"] = trimmed.get("next_reading", [])[:Budgets.TURN_MAX_POINTERS]
    return trimmed
