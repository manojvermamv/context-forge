from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Optional


class AuthorityDomain(str, Enum):
    """Typed authority domains governing what knowledge can establish."""
    INTENT = "INTENT"                   # Decisions (ADRs), Requirements, Goals set by humans/stakeholders
    POLICY = "POLICY"                   # Invariants, security rules, compliance boundaries
    IMPLEMENTATION = "IMPLEMENTATION"   # Current code structure, AST, symbols, tests, runtime evidence
    EXPERIENCE = "EXPERIENCE"           # Agent procedural memory, session lessons, historical observations


class ResolutionDisposition(str, Enum):
    """Classification of cross-plane contradictions."""
    ADVICE_REJECTED = "ADVICE_REJECTED"     # Intent/Policy dictates; Experience memory is subordinate advice
    DRIFT = "DRIFT"                         # Code implementation violates stated Intent/Policy (active drift alert)
    RECORD_STALE = "RECORD_STALE"           # Fresh code truth contradicts older recorded technical fact
    SUPERSEDED = "SUPERSEDED"               # Newer authoritative ADR supersedes older ADR
    USER_OVERRIDE = "USER_OVERRIDE"         # Current direct user instruction supersedes past decisions
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH" # Test/Runtime verification refutes technical claim


class AuthorityLevel(IntEnum):
    """Epistemic authority ranking across knowledge sources."""
    USER_EXPLICIT_CURRENT = 100
    USER_EXPLICIT_ACCEPTED = 90
    POLICY_MANDATE = 85
    CODE_CURRENT_EVIDENCE = 80
    CODE_OBSERVED_ACCEPTED = 70
    EXTERNAL_DOC_TRUSTED = 60
    AGENT_PROCEDURAL_MEMORY = 50
    AGENT_EPISODIC_MEMORY = 40
    AGENT_INFERENCE = 20
    UNRESOLVED = 10


AUTHORITY_MAP = {
    "user_explicit": AuthorityLevel.USER_EXPLICIT_ACCEPTED,
    "policy_mandate": AuthorityLevel.POLICY_MANDATE,
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

    @staticmethod
    def get_domain(kind: str = "", authority: str = "") -> AuthorityDomain:
        """Derive typed authority domain from record kind and authority."""
        k = kind.lower()
        a = authority.lower()
        if k in ("decision", "requirement", "goal") or a in ("user_explicit",):
            return AuthorityDomain.INTENT
        if k in ("policy", "security", "invariant") or a in ("policy_mandate",):
            return AuthorityDomain.POLICY
        if k in ("technical", "traceability", "code_evidence") or a in ("code_observed", "derived_link"):
            return AuthorityDomain.IMPLEMENTATION
        if a in ("procedural_memory", "episodic_memory", "agent_inference") or k in ("memory", "lesson", "finding"):
            return AuthorityDomain.EXPERIENCE
        return AuthorityDomain.INTENT if a == "user_explicit" else AuthorityDomain.EXPERIENCE

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
        """Return 1 if A wins, -1 if B wins, 0 if equal rank (legacy ranking)."""
        lvl_a = cls.get_level(authority_a)
        lvl_b = cls.get_level(authority_b)
        if lvl_a > lvl_b:
            return 1
        elif lvl_b > lvl_a:
            return -1
        return 0

    @classmethod
    def resolve_domains(
        cls,
        domain_a: AuthorityDomain,
        domain_b: AuthorityDomain,
        level_a: int = 0,
        level_b: int = 0,
    ) -> ResolutionDisposition:
        """Determine correct disposition for cross-domain interactions."""
        # 1. Intent/Policy vs Experience: Intent rules, experience is subordinate advice
        if (domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b == AuthorityDomain.EXPERIENCE):
            return ResolutionDisposition.ADVICE_REJECTED
        if (domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_a == AuthorityDomain.EXPERIENCE):
            return ResolutionDisposition.ADVICE_REJECTED

        # 2. Intent/Policy vs Implementation: DRIFT / VIOLATION
        # Code does NOT change intent, and intent does NOT hide reality
        if (domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b == AuthorityDomain.IMPLEMENTATION):
            return ResolutionDisposition.DRIFT
        if (domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_a == AuthorityDomain.IMPLEMENTATION):
            return ResolutionDisposition.DRIFT

        # 3. Implementation vs Implementation (e.g. fresh code truth vs old technical record)
        if domain_a == AuthorityDomain.IMPLEMENTATION and domain_b == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.RECORD_STALE

        # 4. Intent vs Intent (e.g. superseding ADR or user explicit instruction)
        if domain_a == AuthorityDomain.INTENT and domain_b == AuthorityDomain.INTENT:
            if level_a > level_b:
                return ResolutionDisposition.USER_OVERRIDE if level_a >= AuthorityLevel.USER_EXPLICIT_CURRENT else ResolutionDisposition.SUPERSEDED
            return ResolutionDisposition.SUPERSEDED

        return ResolutionDisposition.ADVICE_REJECTED
