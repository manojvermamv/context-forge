from __future__ import annotations

import re
from typing import Any, Optional
from context_forge.core.authority import AuthorityDomain, EpistemicAuthority, ResolutionDisposition


class ConflictResolver:
    """Reconciles conflicting assertions across Project Truth (Intent/Policy), Code Truth (Implementation), and Agent Experience."""

    @staticmethod
    def detect_conflicts(
        authoritative: list[dict[str, Any]],
        experience: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when agent experiential memory contradicts an authoritative ADR or requirement (Intent vs Experience)."""
        conflicts = []
        for adr in authoritative:
            body = (adr.get("body", "") + " " + adr.get("title", "")).lower()
            for exp in experience:
                finding = (exp.get("finding", "") + " " + exp.get("lesson", "")).lower()
                # Check for explicit contradiction triggers (e.g., rejected, do not use, deprecated)
                for neg in ("rejected", "do not use", "forbidden", "disabled", "superseded", "avoid"):
                    if neg in body:
                        words = [w for w in body.split() if len(w) > 4 and w != neg]
                        for w in words:
                            if w in finding and ("use" in finding or "recommended" in finding):
                                conflicts.append({
                                    "warning": (
                                        f"Conflict detected: Agent memory suggested using '{w}', "
                                        f"but authoritative {adr.get('id')} explicitly dictates: '{adr.get('title')}'. "
                                        "Authoritative intent strictly wins."
                                    ),
                                    "winner": adr.get("id"),
                                    "subordinate": exp.get("source", "memory"),
                                    "disposition": ResolutionDisposition.ADVICE_REJECTED.value,
                                    "domain_authoritative": AuthorityDomain.INTENT.value,
                                    "domain_subordinate": AuthorityDomain.EXPERIENCE.value,
                                })
                                break
        return conflicts

    @staticmethod
    def detect_code_drift(
        authoritative: list[dict[str, Any]],
        implementation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when actual code implementation violates or drifts from authoritative intent/policy.
        
        Intent vs Implementation produces DRIFT / VIOLATION, NOT 'intent wins, ignore code'.
        Both are retained with their epistemic domains clearly designated.
        """
        drifts = []
        for adr in authoritative:
            adr_id = adr.get("id", "ADR")
            adr_title = adr.get("title", "")
            adr_text = (adr.get("body", "") + " " + adr_title).lower()

            for fact in implementation:
                details = (
                    fact.get("details", "")
                    + " " + fact.get("finding", "")
                    + " " + fact.get("symbol", "")
                    + " " + fact.get("summary", "")
                ).lower()

                # Look for violations: e.g. "bypasses", "violates", "missing", "direct access"
                # or negation of mandated keywords
                is_drift = False
                trigger_reason = ""

                for mandate_word in ("must", "required", "mandatory", "enforce", "pass"):
                    if mandate_word in adr_text:
                        # Extract what is mandated
                        for token in re.findall(r"\b[A-Za-z0-9_]{4,30}\b", adr_text):
                            if token in details and any(b in details for b in ("bypass", "skips", "missing", "violat", "omits", "without")):
                                is_drift = True
                                trigger_reason = f"Implementation bypasses or omits mandated '{token}'"
                                break

                # Also check direct prohibition: e.g. "forbidden", "do not", "rejected"
                for forbid_word in ("forbidden", "prohibited", "disallowed", "cannot"):
                    if forbid_word in adr_text:
                        for token in re.findall(r"\b[A-Za-z0-9_]{4,30}\b", adr_text):
                            if token in details and any(u in details for u in ("calls", "uses", "contains", "present", "invokes")):
                                is_drift = True
                                trigger_reason = f"Implementation uses prohibited element '{token}'"
                                break

                if is_drift:
                    drifts.append({
                        "type": "DRIFT",
                        "disposition": ResolutionDisposition.DRIFT.value,
                        "intent_id": adr_id,
                        "intent_title": adr_title,
                        "intent_domain": AuthorityDomain.INTENT.value,
                        "implementation_domain": AuthorityDomain.IMPLEMENTATION.value,
                        "reality": fact.get("details") or fact.get("finding") or details,
                        "warning": (
                            f"DRIFT / VIOLATION: {adr_id} mandates '{adr_title}', "
                            f"but current code reality indicates: {trigger_reason} ({fact.get('symbol') or fact.get('path', 'unknown')}). "
                            "Implementation violates intent. Code does not alter policy, and intent does not hide code reality."
                        ),
                        "symbol": fact.get("symbol", ""),
                        "path": fact.get("path", ""),
                    })
        return drifts

    @staticmethod
    def detect_stale_records(
        technical_records: list[dict[str, Any]],
        fresh_code_facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when fresh live code observations contradict older recorded technical facts.
        
        Implementation (Live) vs Implementation (Old Doc) -> RECORD_STALE.
        Live code evidence establishes current implementation reality; doc is marked stale.
        """
        stale_records = []
        for tech in technical_records:
            tech_id = tech.get("id", "TECH")
            tech_body = (tech.get("body", "") + " " + tech.get("title", "")).lower()

            for fact in fresh_code_facts:
                fact_text = (fact.get("details", "") + " " + fact.get("finding", "")).lower()
                # Check for contradiction on same symbol or path
                tech_paths = set(tech.get("scope", []))
                fact_path = fact.get("path", "")
                if fact_path and fact_path in tech_paths:
                    # Compare content signals
                    if "removed" in fact_text or "renamed" in fact_text or "deprecated" in fact_text:
                        stale_records.append({
                            "type": "RECORD_STALE",
                            "disposition": ResolutionDisposition.RECORD_STALE.value,
                            "stale_id": tech_id,
                            "winner": "current_code_evidence",
                            "warning": (
                                f"STALE TECHNICAL RECORD: {tech_id} is contradicted by current code evidence at '{fact_path}'. "
                                f"Fresh code observation supersedes recorded documentation."
                            ),
                            "live_evidence": fact_text,
                        })
        return stale_records

    @classmethod
    def resolve_cross_plane(cls, item_a: dict[str, Any], item_b: dict[str, Any]) -> dict[str, Any]:
        """Resolve arbitrary pairwise interaction across authority domains."""
        domain_a = EpistemicAuthority.get_domain(item_a.get("kind", ""), item_a.get("authority", ""))
        domain_b = EpistemicAuthority.get_domain(item_b.get("kind", ""), item_b.get("authority", ""))
        level_a = int(item_a.get("authority_level", EpistemicAuthority.get_level(item_a.get("authority", ""))))
        level_b = int(item_b.get("authority_level", EpistemicAuthority.get_level(item_b.get("authority", ""))))

        disposition = EpistemicAuthority.resolve_domains(domain_a, domain_b, level_a, level_b)

        if disposition == ResolutionDisposition.ADVICE_REJECTED:
            intent_item = item_a if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else item_b
            exp_item = item_b if intent_item is item_a else item_a
            return {
                "disposition": disposition.value,
                "winner": intent_item.get("id", "intent"),
                "subordinate": exp_item.get("id") or exp_item.get("source", "experience"),
                "warning": f"Intent dictates policy: {intent_item.get('id')} overrides experience advice {exp_item.get('id', '')}.",
            }
        elif disposition == ResolutionDisposition.DRIFT:
            intent_item = item_a if domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else item_b
            code_item = item_b if intent_item is item_a else item_a
            return {
                "disposition": disposition.value,
                "intent": intent_item.get("id", "intent"),
                "reality": code_item.get("symbol") or code_item.get("path") or code_item.get("details", ""),
                "warning": f"DRIFT / VIOLATION: {intent_item.get('id')} mandates '{intent_item.get('title')}', but code reality diverges.",
            }
        elif disposition == ResolutionDisposition.RECORD_STALE:
            return {
                "disposition": disposition.value,
                "winner": "current_code_evidence",
                "stale_record": item_a.get("id") if not item_a.get("is_live") else item_b.get("id"),
                "warning": "Live code evidence refutes technical documentation.",
            }
        elif disposition in (ResolutionDisposition.SUPERSEDED, ResolutionDisposition.USER_OVERRIDE):
            winner = item_a if level_a >= level_b else item_b
            subordinate = item_b if winner is item_a else item_a
            return {
                "disposition": disposition.value,
                "winner": winner.get("id", "newer_intent"),
                "subordinate": subordinate.get("id", "older_intent"),
                "warning": f"{disposition.value}: {winner.get('id')} supersedes {subordinate.get('id')}.",
            }

        return {"disposition": disposition.value}

