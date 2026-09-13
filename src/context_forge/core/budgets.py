from __future__ import annotations

import os


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Budgets:
    """Character budgets enforced by Context Forge to guarantee progressive disclosure."""
    L0_CHARS: int = _envint("BRAIN_L0_BUDGET", 1600)             # current-state.md cold-start cap
    MAP_CHARS: int = _envint("BRAIN_MAP_BUDGET", 4000)           # map.md cap
    TOPIC_CHARS: int = _envint("BRAIN_TOPIC_BUDGET", 6000)       # concept/decision page cap
    CONTEXT_PACK_CHARS: int = _envint("BRAIN_CONTEXT_PACK_BUDGET", 5000) # context pack compilation cap
    TURN_MAX_POINTERS: int = _envint("BRAIN_TURN_MAX_POINTERS", 3)
    REVIEW_MAX_PAGES: int = _envint("BRAIN_REVIEW_MAX_PAGES", 60)
    REVIEW_INTERVAL_DAYS: int = _envint("BRAIN_REVIEW_INTERVAL_DAYS", 7)
    LOG_ROTATE_DAYS: int = _envint("BRAIN_LOG_ROTATE_DAYS", 14)
    MAX_INDEX_FILE_BYTES: int = _envint("BRAIN_MAX_INDEX_BYTES", 256 * 1024)
    CAPTURE_TAIL_BYTES: int = _envint("BRAIN_CAPTURE_TAIL_BYTES", 8 * 1024)
    INJECTION_SUPPRESSION_SECONDS: int = _envint("BRAIN_INJECTION_SUPPRESSION_SECONDS", 15)

    @classmethod
    def rough_tokens(cls, chars: int) -> int:
        """Estimate token count from characters using 3.8 chars/token ratio."""
        return max(1, round(chars / 3.8))

    @classmethod
    def char_budget_note(cls, label: str, n: int, budget: int) -> str:
        pct = int(100 * n / budget) if budget else 0
        flag = " ⚠ OVER BUDGET" if n > budget else ""
        return f"{label}: {n} chars / {budget} budget ({pct}%){flag}"
