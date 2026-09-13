from __future__ import annotations

import re
from typing import Any, Optional
from context_forge.core.authority import (
    AuthorityDomain,
    EpistemicAuthority,
    ResolutionDisposition,
    AuthorityResolution,
)


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
                                    "reconciliation_required": False,
                                })
                                break
        return conflicts

    @staticmethod
    def detect_code_drift(
        authoritative: list[dict[str, Any]],
        implementation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when actual code implementation violates or drifts from authoritative intent/policy."""
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

                is_drift = False
                trigger_reason = ""

                for mandate_word in ("must", "required", "mandatory", "enforce", "pass"):
                    if mandate_word in adr_text:
                        for token in re.findall(r"\b[A-Za-z0-9_]{4,30}\b", adr_text):
                            if token in details and any(b in details for b in ("bypass", "skips", "missing", "violat", "omits", "without")):
                                is_drift = True
                                trigger_reason = f"Implementation bypasses or omits mandated '{token}'"
                                break

                for forbid_word in ("forbidden", "prohibited", "disallowed", "cannot"):
                    if forbid_word in adr_text:
                        for token in re.findall(r"\b[A-Za-z0-9_]{4,30}\b", adr_text):
                            if token in details and any(u in details for u in ("calls", "uses", "contains", "present", "invokes")):
                                is_drift = True
                                trigger_reason = f"Implementation uses prohibited element '{token}'"
                                break

                if is_drift:
                    disp = ResolutionDisposition.VIOLATION.value if "policy" in adr.get("kind", "").lower() else ResolutionDisposition.DRIFT.value
                    drifts.append({
                        "type": disp,
                        "disposition": disp,
                        "intent_id": adr_id,
                        "intent_title": adr_title,
                        "intent_domain": AuthorityDomain.INTENT.value,
                        "implementation_domain": AuthorityDomain.IMPLEMENTATION.value,
                        "reality": fact.get("details") or fact.get("finding") or details,
                        "warning": (
                            f"{disp}: {adr_id} mandates '{adr_title}', "
                            f"but current code reality indicates: {trigger_reason} ({fact.get('symbol') or fact.get('path', 'unknown')}). "
                            "Implementation violates intent. Code does not alter policy, and intent does not hide code reality."
                        ),
                        "symbol": fact.get("symbol", ""),
                        "path": fact.get("path", ""),
                        "reconciliation_required": True,
                    })
        return drifts

    @staticmethod
    def detect_stale_records(
        technical_records: list[dict[str, Any]],
        fresh_code_facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when fresh live code observations contradict older recorded technical facts."""
        stale_records = []
        for tech in technical_records:
            tech_id = tech.get("id", "TECH")
            tech_paths = set(tech.get("scope", []))

            for fact in fresh_code_facts:
                fact_text = (fact.get("details", "") + " " + fact.get("finding", "")).lower()
                fact_path = fact.get("path", "")
                if fact_path and fact_path in tech_paths:
                    if any(w in fact_text for w in ("removed", "renamed", "deprecated", "deleted", "async")):
                        stale_records.append({
                            "type": "RECORD_STALE",
                            "disposition": ResolutionDisposition.RECORD_STALE.value,
                            "stale_id": tech_id,
                            "winner": "current_code_evidence",
                            "warning": (
                                f"STALE TECHNICAL RECORD: {tech_id} is contradicted by current code evidence at '{fact_path}'. "
                                "Fresh code observation supersedes recorded documentation."
                            ),
                            "live_evidence": fact_text,
                            "reconciliation_required": True,
                        })
        return stale_records

    @classmethod
    def resolve_cross_plane(cls, item_a: dict[str, Any], item_b: dict[str, Any]) -> dict[str, Any]:
        """Resolve arbitrary pairwise interaction across authority domains."""
        resolution = EpistemicAuthority.resolve_claims(item_a, item_b)
        result = resolution.to_dict()

        if resolution.disposition == ResolutionDisposition.ADVICE_REJECTED:
            intent_item = item_a if resolution.domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else item_b
            exp_item = item_b if intent_item is item_a else item_a
            result["winner"] = intent_item.get("id", "intent")
            result["subordinate"] = exp_item.get("id") or exp_item.get("source", "experience")
            result["warning"] = f"Intent dictates policy: {intent_item.get('id')} overrides experience advice {exp_item.get('id', '')}."
        elif resolution.disposition in (ResolutionDisposition.DRIFT, ResolutionDisposition.VIOLATION):
            intent_item = item_a if resolution.domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else item_b
            code_item = item_b if intent_item is item_a else item_a
            result["intent"] = intent_item.get("id", "intent")
            result["reality"] = code_item.get("symbol") or code_item.get("path") or code_item.get("details", "")
            result["warning"] = f"{resolution.disposition.value}: {intent_item.get('id')} mandates '{intent_item.get('title')}', but code reality diverges."
        elif resolution.disposition == ResolutionDisposition.RECORD_STALE:
            result["winner"] = "current_code_evidence"
            result["stale_record"] = item_a.get("id") if not item_a.get("is_live") else item_b.get("id")
            result["warning"] = "Live code evidence refutes technical documentation."
        elif resolution.disposition in (ResolutionDisposition.SUPERSEDED, ResolutionDisposition.USER_OVERRIDE):
            winner = resolution.winner or item_a.get("id")
            subordinate = item_b.get("id") if winner == item_a.get("id") else item_a.get("id")
            result["subordinate"] = subordinate
            result["warning"] = f"{resolution.disposition.value}: {winner} supersedes {subordinate}."

        return result
