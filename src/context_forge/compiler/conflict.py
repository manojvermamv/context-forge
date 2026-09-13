from __future__ import annotations

from typing import Any


class ConflictResolver:
    """Reconciles conflicting assertions across Project Truth, Code Truth, and Agent Experience."""

    @staticmethod
    def detect_conflicts(
        authoritative: list[dict[str, Any]],
        experience: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Identify when agent experiential memory contradicts an authoritative ADR or requirement."""
        conflicts = []
        for adr in authoritative:
            body = (adr.get("body", "") + " " + adr.get("title", "")).lower()
            for exp in experience:
                finding = (exp.get("finding", "") + " " + exp.get("lesson", "")).lower()
                # Check for explicit contradiction triggers (e.g., rejected, do not use, deprecated)
                for neg in ("rejected", "do not use", "forbidden", "disabled", "superseded", "avoid"):
                    if neg in body:
                        # Extract the key subject of negation
                        words = [w for w in body.split() if len(w) > 4 and w != neg]
                        for w in words:
                            if w in finding and "use" in finding:
                                conflicts.append({
                                    "warning": (
                                        f"Conflict detected: Agent memory suggested using '{w}', "
                                        f"but authoritative {adr.get('id')} explicitly dictates: '{adr.get('title')}'. "
                                        "Authoritative intent strictly wins."
                                    ),
                                    "winner": adr.get("id"),
                                    "subordinate": exp.get("source", "memory"),
                                })
                                break
        return conflicts
