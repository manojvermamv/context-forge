from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional


class AuthorityDomain(str, Enum):
    """Typed authority domains governing what knowledge can establish."""
    INTENT = "INTENT"                   # Decisions (ADRs), Requirements, Goals set by humans/stakeholders
    POLICY = "POLICY"                   # Invariants, security rules, compliance boundaries
    IMPLEMENTATION = "IMPLEMENTATION"   # Current code structure, AST, symbols, tests, runtime evidence
    EXPERIENCE = "EXPERIENCE"           # Agent procedural memory, session lessons, historical observations


class ResolutionDisposition(str, Enum):
    """Classification of cross-plane and intra-plane claim comparisons."""
    AGREES = "AGREES"                       # Claims are consistent or mutually supportive
    ADVICE_REJECTED = "ADVICE_REJECTED"     # Intent/Policy dictates; Experience memory is subordinate advice
    USER_OVERRIDE = "USER_OVERRIDE"         # Current direct user instruction supersedes past decisions
    DRIFT = "DRIFT"                         # Code implementation diverges from stated Intent
    VIOLATION = "VIOLATION"                 # Code implementation breaches mandatory Policy/Invariant
    RECORD_STALE = "RECORD_STALE"           # Fresh code truth contradicts older recorded technical fact
    SUPERSEDED = "SUPERSEDED"               # Newer authoritative record formally replaces older record
    CONTRADICTED = "CONTRADICTED"           # Conflicting claims within same domain without clear precedence
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH" # Test/Runtime verification refutes technical claim
    UNRESOLVED = "UNRESOLVED"               # Incomplete or ambiguous claims requiring reconciliation
    INCOMPARABLE = "INCOMPARABLE"           # Non-overlapping scopes or orthogonal domains


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


@dataclass
class AuthorityResolution:
    """Structured resolution of pairwise authority interactions."""
    disposition: ResolutionDisposition
    domain_a: AuthorityDomain
    domain_b: AuthorityDomain
    claims_conflict: bool
    winner: Optional[str] = None
    preserved_claims: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    evidence: str = ""
    reconciliation_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "domain_a": self.domain_a.value,
            "domain_b": self.domain_b.value,
            "claims_conflict": self.claims_conflict,
            "winner": self.winner,
            "preserved_claims": self.preserved_claims,
            "reason": self.reason,
            "evidence": self.evidence,
            "reconciliation_required": self.reconciliation_required,
        }


class EpistemicAuthority:
    """Validator for authority transitions, promotions, and conflicts."""

    @staticmethod
    def get_level(authority_name: str) -> AuthorityLevel:
        return AUTHORITY_MAP.get(authority_name, AuthorityLevel.UNRESOLVED)

    @staticmethod
    def get_domain(kind: str = "", authority: str = "", item: Optional[dict[str, Any]] = None) -> AuthorityDomain:
        """Derive typed authority domain from record kind, authority, or claim dictionary."""
        k = kind.lower()
        a = authority.lower()
        if item:
            item_id = str(item.get("id", "")).upper()
            if not k:
                if item_id.startswith(("ADR", "REQ", "GOAL", "USER")):
                    k = "decision"
                elif item_id.startswith(("POL", "INV")):
                    k = "policy"
                elif item_id.startswith(("TECH", "TRACE", "SYM")):
                    k = "technical"
            if not a:
                if item.get("source") == "agentmemory" or "finding" in item or "lesson" in item or item_id.startswith("MEM"):
                    a = "procedural_memory"
                elif "symbol" in item or "details" in item or "is_live" in item:
                    a = "code_observed"
                elif k in ("decision", "requirement"):
                    a = "user_explicit"
                elif k in ("policy", "invariant"):
                    a = "policy_mandate"

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
        elif kind in ("policy", "invariant"):
            if authority not in ("policy_mandate", "user_explicit"):
                return False, f"{kind} requires --authority policy_mandate or user_explicit"
            if not accept:
                return False, f"{kind} requires explicit acceptance (--accept)"
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
        """Determine default potential disposition from domain interaction alone."""
        if (domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b == AuthorityDomain.EXPERIENCE):
            return ResolutionDisposition.ADVICE_REJECTED
        if (domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_a == AuthorityDomain.EXPERIENCE):
            return ResolutionDisposition.ADVICE_REJECTED

        if domain_a == AuthorityDomain.POLICY and domain_b == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.VIOLATION
        if domain_b == AuthorityDomain.POLICY and domain_a == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.VIOLATION

        if domain_a == AuthorityDomain.INTENT and domain_b == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.DRIFT
        if domain_b == AuthorityDomain.INTENT and domain_a == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.DRIFT

        if domain_a == AuthorityDomain.IMPLEMENTATION and domain_b == AuthorityDomain.IMPLEMENTATION:
            return ResolutionDisposition.AGREES

        if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY):
            if level_a >= AuthorityLevel.USER_EXPLICIT_CURRENT and level_a > level_b:
                return ResolutionDisposition.USER_OVERRIDE
            if level_b >= AuthorityLevel.USER_EXPLICIT_CURRENT and level_b > level_a:
                return ResolutionDisposition.USER_OVERRIDE
            return ResolutionDisposition.AGREES

        return ResolutionDisposition.AGREES

    @classmethod
    def resolve_claims(
        cls,
        claim_a: dict[str, Any],
        claim_b: dict[str, Any],
    ) -> AuthorityResolution:
        """Perform robust pairwise resolution separating claim comparison from domain relationship."""
        domain_a = cls.get_domain(claim_a.get("kind", ""), claim_a.get("authority", ""), claim_a)
        domain_b = cls.get_domain(claim_b.get("kind", ""), claim_b.get("authority", ""), claim_b)
        lvl_a = int(claim_a.get("authority_level") or cls.get_level(claim_a.get("authority", "")))
        lvl_b = int(claim_b.get("authority_level") or cls.get_level(claim_b.get("authority", "")))

        scopes_a = set(claim_a.get("scope") or ([claim_a["path"]] if "path" in claim_a else []))
        scopes_b = set(claim_b.get("scope") or ([claim_b["path"]] if "path" in claim_b else []))
        symbols_a = set(claim_a.get("symbols") or ([claim_a["symbol"]] if "symbol" in claim_a else []))
        symbols_b = set(claim_b.get("symbols") or ([claim_b["symbol"]] if "symbol" in claim_b else []))

        text_a = (claim_a.get("body", "") + " " + claim_a.get("title", "") + " " + claim_a.get("details", "") + " " + claim_a.get("finding", "")).lower()
        text_b = (claim_b.get("body", "") + " " + claim_b.get("title", "") + " " + claim_b.get("details", "") + " " + claim_b.get("finding", "")).lower()

        # Check scope overlap
        has_scope_overlap = bool((scopes_a and scopes_b and (scopes_a & scopes_b)) or (symbols_a and symbols_b and (symbols_a & symbols_b)))

        # 1. INTENT / POLICY vs EXPERIENCE
        if (domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b == AuthorityDomain.EXPERIENCE) or \
           (domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_a == AuthorityDomain.EXPERIENCE):
            intent_item = claim_a if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else claim_b
            exp_item = claim_b if intent_item is claim_a else claim_a

            intent_text = (intent_item.get("body", "") + " " + intent_item.get("title", "")).lower()
            exp_text = (exp_item.get("finding", "") + " " + exp_item.get("lesson", "") + " " + exp_item.get("title", "")).lower()

            # Check normalized structured propositions first
            subj_i = intent_item.get("subject") or intent_item.get("claim_key")
            subj_e = exp_item.get("subject") or exp_item.get("claim_key")
            if subj_i and subj_e and str(subj_i).lower() == str(subj_e).lower():
                pol_i = intent_item.get("polarity", True)
                pol_e = exp_item.get("polarity", True)
                obj_i = str(intent_item.get("object", "")).lower()
                obj_e = str(exp_item.get("object", "")).lower()
                pred_i = str(intent_item.get("predicate", "")).lower()
                pred_e = str(exp_item.get("predicate", "")).lower()

                if pol_i != pol_e or (obj_i and obj_e and obj_i != obj_e):
                    return AuthorityResolution(
                        disposition=ResolutionDisposition.ADVICE_REJECTED,
                        domain_a=domain_a,
                        domain_b=domain_b,
                        claims_conflict=True,
                        winner=intent_item.get("id"),
                        preserved_claims=[intent_item],
                        reason=f"Authoritative {intent_item.get('id')} dictates intent for '{subj_i}'; contradictory experiential advice rejected.",
                        evidence=f"Intent: {pred_i} {obj_i} (polarity={pol_i}); Experience: {pred_e} {obj_e} (polarity={pol_e})",
                        reconciliation_required=False,
                    )
                else:
                    return AuthorityResolution(
                        disposition=ResolutionDisposition.AGREES,
                        domain_a=domain_a,
                        domain_b=domain_b,
                        claims_conflict=False,
                        preserved_claims=[claim_a, claim_b],
                        reason="Experiential finding agrees with authoritative structured proposition.",
                    )

            # Phase 1: Relevance check
            intent_words = set(re.findall(r"\b[a-zA-Z0-9_-]{4,30}\b", intent_text))
            exp_words = set(re.findall(r"\b[a-zA-Z0-9_-]{4,30}\b", exp_text))
            stop_words = {"with", "that", "this", "from", "have", "will", "your", "must", "should", "could", "would"}
            shared_topics = (intent_words & exp_words) - stop_words

            # Phase 2: Contradiction check via semantic polarity
            advice_contradicts = False
            for neg in ("rejected", "do not use", "forbidden", "disabled", "superseded", "avoid", "must not", "prohibited", "disallow"):
                if neg in intent_text:
                    for w in shared_topics:
                        if w in exp_text and any(k in exp_text for k in ("use", "try", "recommended", "adopt", "suggest")):
                            advice_contradicts = True
                            break

            if advice_contradicts:
                return AuthorityResolution(
                    disposition=ResolutionDisposition.ADVICE_REJECTED,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=intent_item.get("id"),
                    preserved_claims=[intent_item],
                    reason=f"Authoritative {intent_item.get('id')} dictates intent; contradictory experiential memory is subordinate advice.",
                    evidence="Authoritative intent subordinates conflicting experiential advice.",
                    reconciliation_required=False,
                )

            # Compatible, unresolved, or incomparable
            if shared_topics or has_scope_overlap:
                return AuthorityResolution(
                    disposition=ResolutionDisposition.AGREES,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=False,
                    preserved_claims=[claim_a, claim_b],
                    reason="Experiential finding is compatible with authoritative intent.",
                )
            return AuthorityResolution(
                disposition=ResolutionDisposition.INCOMPARABLE,
                domain_a=domain_a,
                domain_b=domain_b,
                claims_conflict=False,
                preserved_claims=[claim_a, claim_b],
                reason="Unrelated intent and experience domains.",
            )

        # 2. INTENT / POLICY vs IMPLEMENTATION
        if (domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b == AuthorityDomain.IMPLEMENTATION) or \
           (domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_a == AuthorityDomain.IMPLEMENTATION):
            intent_item = claim_a if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else claim_b
            code_item = claim_b if intent_item is claim_a else claim_a
            intent_domain = domain_a if intent_item is claim_a else domain_b
            code_text = text_b if intent_item is claim_a else text_a
            intent_text = (intent_item.get("body", "") + " " + intent_item.get("title", "")).lower()

            is_violation = intent_domain == AuthorityDomain.POLICY
            disp = ResolutionDisposition.VIOLATION if is_violation else ResolutionDisposition.DRIFT

            # Check normalized structured propositions
            subj_i = intent_item.get("subject") or intent_item.get("claim_key") or (intent_item.get("claim", {}).get("subject") if isinstance(intent_item.get("claim"), dict) else getattr(intent_item.get("claim"), "subject", None))
            subj_c = code_item.get("subject") or code_item.get("claim_key") or (code_item.get("claim", {}).get("subject") if isinstance(code_item.get("claim"), dict) else getattr(code_item.get("claim"), "subject", None))
            if subj_i and subj_c and str(subj_i).lower() == str(subj_c).lower():
                pol_i = intent_item.get("polarity", True)
                if isinstance(intent_item.get("claim"), dict) and "polarity" in intent_item["claim"]:
                    pol_i = intent_item["claim"]["polarity"]
                pol_c = code_item.get("polarity", True)
                if isinstance(code_item.get("claim"), dict) and "polarity" in code_item["claim"]:
                    pol_c = code_item["claim"]["polarity"]

                obj_i = str(intent_item.get("object") or (intent_item.get("claim", {}).get("object") if isinstance(intent_item.get("claim"), dict) else "")).lower()
                obj_c = str(code_item.get("object") or (code_item.get("claim", {}).get("object") if isinstance(code_item.get("claim"), dict) else "")).lower()
                pred_i = str(intent_item.get("predicate") or (intent_item.get("claim", {}).get("predicate") if isinstance(intent_item.get("claim"), dict) else "")).lower()
                pred_c = str(code_item.get("predicate") or (code_item.get("claim", {}).get("predicate") if isinstance(code_item.get("claim"), dict) else "")).lower()

                if pol_i != pol_c or (obj_i and obj_c and obj_i != obj_c):
                    return AuthorityResolution(
                        disposition=disp,
                        domain_a=domain_a,
                        domain_b=domain_b,
                        claims_conflict=True,
                        winner=None,
                        preserved_claims=[intent_item, code_item],
                        reason=f"{disp.value}: Implementation diverges from structured proposition '{subj_i}'.",
                        evidence=f"Intent: {pred_i} {obj_i} (polarity={pol_i}); Code: {pred_c} {obj_c} (polarity={pol_c})",
                        reconciliation_required=True,
                    )
                else:
                    return AuthorityResolution(
                        disposition=ResolutionDisposition.AGREES,
                        domain_a=domain_a,
                        domain_b=domain_b,
                        claims_conflict=False,
                        preserved_claims=[claim_a, claim_b],
                        reason="Implementation aligns with authoritative structured proposition.",
                    )

            # Phase 1: Identify intent subjects and scope
            intent_subjects: set[str] = set()
            if intent_item.get("id"):
                intent_subjects.add(str(intent_item["id"]).lower())
            if subj_i:
                intent_subjects.add(str(subj_i).lower())
            for sym in (intent_item.get("symbols") or ([intent_item["symbol"]] if "symbol" in intent_item else [])):
                intent_subjects.add(str(sym).lower())
            raw_intent = intent_item.get("title", "") + " " + intent_item.get("body", "")
            for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,30}\b", intent_item.get("title", "")):
                if token.lower() not in {"must", "should", "with", "from", "that", "this", "have", "will", "and", "the", "for", "not", "into", "onto", "about"}:
                    intent_subjects.add(token.lower())
            for acr in re.findall(r"\b[A-Z]{2,10}\b", raw_intent):
                if acr.lower() not in {"all", "and", "the", "for", "not", "adr", "req", "pol", "inv"}:
                    intent_subjects.add(acr.lower())
            for camel in re.findall(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b", raw_intent):
                intent_subjects.add(camel.lower())
            for mand in re.findall(r"\b(?:enforce|enforces|require|requires|mandate|mandates|use|uses)\s+([A-Za-z0-9_-]{2,20})\b", raw_intent, re.IGNORECASE):
                if mand.lower() not in {"that", "this", "all", "the", "a", "an", "any"}:
                    intent_subjects.add(mand.lower())

            # Phase 2: Conservative fallback for unstructured claims
            # Contradiction requires BOTH:
            # 1. Subject relevance (code mentions intent subject, or shares scope/symbol)
            # 2. Contradiction expression is explicitly bound to the subject (e.g. "without RiskGate")
            # Unbound negation words ("without allocating a temporary buffer") must NEVER create false DRIFT.
            bound_violation = False
            violation_evidence = ""
            negation_verbs = r"(?:without|bypass(?:ing|ed|es)?|skips?|missing|disables?|omits?|breach(?:es|ed)?|violat(?:es|ed|ing)?)"

            for subj in intent_subjects:
                if not subj or len(subj) < 3:
                    continue
                p1 = rf"\b{negation_verbs}\s+(?:\w+\s+){{0,3}}{re.escape(subj)}\b"
                p2 = rf"\b{re.escape(subj)}\s+(?:\w+\s+){{0,3}}(?:bypassed|missing|disabled|omitted|skipped|breached|violated)\b"
                m1 = re.search(p1, code_text)
                m2 = re.search(p2, code_text)
                if m1:
                    bound_violation = True
                    violation_evidence = m1.group(0)
                    break
                if m2:
                    bound_violation = True
                    violation_evidence = m2.group(0)
                    break

            forbid_breached = False
            for f in ("forbidden", "prohibited", "disallowed", "cannot", "must not"):
                if f in intent_text:
                    for subj in intent_subjects:
                        if subj and subj in code_text and any(u in code_text for u in ("calls", "uses", "contains", "invokes")):
                            forbid_breached = True
                            violation_evidence = f"Forbidden subject '{subj}' is invoked or used."
                            break

            if bound_violation or forbid_breached:
                return AuthorityResolution(
                    disposition=disp,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=None,
                    preserved_claims=[intent_item, code_item],
                    reason=f"{'VIOLATION' if is_violation else 'DRIFT'}: Implementation diverges from {intent_item.get('id')}.",
                    evidence=f"Code evidence shows divergence: {violation_evidence or code_text[:120]}",
                    reconciliation_required=True,
                )

            # Implementation in same scope or sharing subject that does NOT violate: AGREES
            if has_scope_overlap or any(subj in code_text for subj in intent_subjects):
                return AuthorityResolution(
                    disposition=ResolutionDisposition.AGREES,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=False,
                    preserved_claims=[claim_a, claim_b],
                    reason="Implementation aligns with authoritative intent (no violation observed).",
                )

            return AuthorityResolution(
                disposition=ResolutionDisposition.INCOMPARABLE,
                domain_a=domain_a,
                domain_b=domain_b,
                claims_conflict=False,
                preserved_claims=[claim_a, claim_b],
                reason="Unrelated intent and implementation scopes.",
            )

        # 3. IMPLEMENTATION vs IMPLEMENTATION
        if domain_a == AuthorityDomain.IMPLEMENTATION and domain_b == AuthorityDomain.IMPLEMENTATION:
            # Check structured proposition
            subj_a = claim_a.get("subject") or claim_a.get("claim_key")
            subj_b = claim_b.get("subject") or claim_b.get("claim_key")
            live_a = claim_a.get("is_live", False) or (claim_a.get("authority") == "code_observed" and not claim_a.get("scope"))
            live_b = claim_b.get("is_live", False) or (claim_b.get("authority") == "code_observed" and not claim_b.get("scope"))

            if subj_a and subj_b and str(subj_a).lower() == str(subj_b).lower():
                pol_a = claim_a.get("polarity", True)
                pol_b = claim_b.get("polarity", True)
                obj_a = str(claim_a.get("object", "")).lower()
                obj_b = str(claim_b.get("object", "")).lower()
                if pol_a != pol_b or (obj_a and obj_b and obj_a != obj_b):
                    winner = "current_code_evidence" if (live_a or live_b) else None
                    return AuthorityResolution(
                        disposition=ResolutionDisposition.RECORD_STALE if (live_a or live_b) else ResolutionDisposition.CONTRADICTED,
                        domain_a=domain_a,
                        domain_b=domain_b,
                        claims_conflict=True,
                        winner=winner,
                        preserved_claims=[claim_b if live_b else claim_a],
                        reason=f"Contradictory technical claim for structured proposition '{subj_a}'.",
                        evidence=f"A: object={obj_a}, pol={pol_a}; B: object={obj_b}, pol={pol_b}",
                        reconciliation_required=True,
                    )

            if not has_scope_overlap and scopes_a and scopes_b and not any(s in text_b for s in scopes_a) and not any(s in text_a for s in scopes_b):
                return AuthorityResolution(
                    disposition=ResolutionDisposition.INCOMPARABLE,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=False,
                    preserved_claims=[claim_a, claim_b],
                    reason="Distinct non-overlapping scopes.",
                )

            # Contradiction signals for technical records: removed, deleted, renamed, deprecated (NO 'async'!)
            contradicts = any(term in text_a for term in ("removed", "deleted", "renamed", "deprecated")) or \
                          any(term in text_b for term in ("removed", "deleted", "renamed", "deprecated"))
            if contradicts and has_scope_overlap:
                winner = "current_code_evidence" if (live_a or live_b) else None
                return AuthorityResolution(
                    disposition=ResolutionDisposition.RECORD_STALE if (live_a or live_b) else ResolutionDisposition.CONTRADICTED,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=winner,
                    preserved_claims=[claim_b if live_b else claim_a],
                    reason="Fresh code observation refutes older technical record.",
                    evidence="Conflicting technical state observed in source.",
                    reconciliation_required=True,
                )
            return AuthorityResolution(
                disposition=ResolutionDisposition.AGREES,
                domain_a=domain_a,
                domain_b=domain_b,
                claims_conflict=False,
                preserved_claims=[claim_a, claim_b],
                reason="Implementation claims are mutually consistent.",
            )

        # 4. INTENT / POLICY vs INTENT / POLICY
        if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) and domain_b in (AuthorityDomain.INTENT, AuthorityDomain.POLICY):
            superseded_by_a = claim_b.get("superseded_by") == claim_a.get("id")
            superseded_by_b = claim_a.get("superseded_by") == claim_b.get("id")

            if superseded_by_a or superseded_by_b:
                winner = claim_a.get("id") if superseded_by_a else claim_b.get("id")
                return AuthorityResolution(
                    disposition=ResolutionDisposition.SUPERSEDED,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=winner,
                    preserved_claims=[claim_a if superseded_by_a else claim_b],
                    reason=f"Explicit supersession: {winner} formally replaces predecessor.",
                    reconciliation_required=False,
                )

            if lvl_a >= AuthorityLevel.USER_EXPLICIT_CURRENT and lvl_a > lvl_b:
                return AuthorityResolution(
                    disposition=ResolutionDisposition.USER_OVERRIDE,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=claim_a.get("id", "current_user_intent"),
                    preserved_claims=[claim_a],
                    reason="Active user prompt explicitly overrides historical intent.",
                    reconciliation_required=False,
                )
            elif lvl_b >= AuthorityLevel.USER_EXPLICIT_CURRENT and lvl_b > lvl_a:
                return AuthorityResolution(
                    disposition=ResolutionDisposition.USER_OVERRIDE,
                    domain_a=domain_a,
                    domain_b=domain_b,
                    claims_conflict=True,
                    winner=claim_b.get("id", "current_user_intent"),
                    preserved_claims=[claim_b],
                    reason="Active user prompt explicitly overrides historical intent.",
                    reconciliation_required=False,
                )

            return AuthorityResolution(
                disposition=ResolutionDisposition.AGREES,
                domain_a=domain_a,
                domain_b=domain_b,
                claims_conflict=False,
                preserved_claims=[claim_a, claim_b],
                reason="Authoritative records are consistent or complementary.",
            )

        return AuthorityResolution(
            disposition=ResolutionDisposition.UNRESOLVED,
            domain_a=domain_a,
            domain_b=domain_b,
            claims_conflict=False,
            preserved_claims=[claim_a, claim_b],
            reason="Unresolved relationship between knowledge claims.",
        )
