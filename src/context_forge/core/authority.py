from __future__ import annotations

from enum import IntEnum
from typing import Optional


class AuthorityLevel(IntEnum):
    """Epistemic authority ranking across knowledge sources."""
    USER_EXPLICIT_CURRENT = 100
    USER_EXPLICIT_ACCEPTED = 90
    CODE_CURRENT_EVIDENCE = 80
    CODE_OBSERVED_ACCEPTED = 70
    EXTERNAL_DOC_TRUSTED = 60
    AGENT_PROCEDURAL_MEMORY = 50
    AGENT_EPISODIC_MEMORY = 40
    AGENT_INFERENCE = 20
    UNRESOLVED = 10


AUTHORITY_MAP = {
    "user_explicit": AuthorityLevel.USER_EXPLICIT_ACCEPTED,
    "code_observed": AuthorityLevel.CODE_OBSERVED_ACCEPTED,
    "derived_link": AuthorityLevel.CODE_OBSERVED_ACCEPTED,
    "external_source": AuthorityLevel.EXTERNAL_DOC_TRUSTED,
    "procedural_memory": AuthorityLevel.AGENT_PROCEDURAL_MEMORY,
    "episodic_memory": AuthorityLevel.AGENT_EPISODIC_MEMORY,
    "agent_inference": AuthorityLevel.AGENT_INFERENCE,
    "unresolved": AuthorityLevel.UNRESOLVED,
}


class EpistemicAuthority:
    """Validator for authority transitions, promotions, and conflicts."""

    @staticmethod
    def get_level(authority_name: str) -> AuthorityLevel:
        return AUTHORITY_MAP.get(authority_name, AuthorityLevel.UNRESOLVED)

    @classmethod
    def can_accept_intent(cls, kind: str, authority: str, accept: bool) -> tuple[bool, Optional[str]]:
        """Validate whether a record kind can be permanently published with accepted status."""
        if kind in ("decision", "requirement"):
            if authority != "user_explicit":
                return False, f"{kind} requires --authority user_explicit. Code or agent inference is not intent."
            if not accept:
                return False, f"{kind} requires explicit acceptance (--accept). Store ambiguity as a question instead."
        elif kind == "technical":
            if authority != "code_observed":
                return False, "technical records must use --authority code_observed"
        elif kind == "question":
            if authority not in ("unresolved", "agent_inference", "user_explicit"):
                return False, "questions must remain unresolved or cite explicit user context"
        return True, None

    @classmethod
    def resolve_conflict(cls, authority_a: str, authority_b: str) -> int:
        """Return 1 if A wins, -1 if B wins, 0 if equal rank."""
        lvl_a = cls.get_level(authority_a)
        lvl_b = cls.get_level(authority_b)
        if lvl_a > lvl_b:
            return 1
        elif lvl_b > lvl_a:
            return -1
        return 0
