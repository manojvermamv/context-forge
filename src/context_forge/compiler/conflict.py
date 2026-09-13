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
            for exp in experience:
                res = EpistemicAuthority.resolve_claims(adr, exp)
                if res.claims_conflict and res.disposition == ResolutionDisposition.ADVICE_REJECTED:
                    conflicts.append({
                        "warning": (
                            f"Conflict detected: Agent memory suggested advice that contradicts "
                            f"authoritative {adr.get('id')} ('{adr.get('title', '')}'). "
                            "Authoritative intent strictly wins."
                        ),
                        "winner": res.winner or adr.get("id"),
                        "subordinate": exp.get("id") or exp.get("source", "memory"),
                        "disposition": res.disposition.value,
                        "domain_authoritative": res.domain_a.value if res.domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else res.domain_b.value,
                        "domain_subordinate": AuthorityDomain.EXPERIENCE.value,
                        "reconciliation_required": res.reconciliation_required,
                    })
        return conflicts

    @staticmethod
    def detect_code_drift(
        authoritative: list[dict[str, Any]],
        implementation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify when actual code implementation violates or drifts from authoritative intent/policy."""
        drifts = []
        for adr in authoritative:
            for fact in implementation:
                res = EpistemicAuthority.resolve_claims(adr, fact)
                if res.claims_conflict and res.disposition in (ResolutionDisposition.DRIFT, ResolutionDisposition.VIOLATION):
                    disp = res.disposition.value
                    adr_id = adr.get("id", "ADR")
                    adr_title = adr.get("title", "")
                    drifts.append({
                        "type": disp,
                        "disposition": disp,
                        "intent_id": adr_id,
                        "intent_title": adr_title,
                        "intent_domain": res.domain_a.value if res.domain_a in (AuthorityDomain.INTENT, AuthorityDomain.POLICY) else res.domain_b.value,
                        "implementation_domain": AuthorityDomain.IMPLEMENTATION.value,
                        "reality": fact.get("details") or fact.get("finding") or fact.get("summary", ""),
                        "warning": (
                            f"{disp}: {adr_id} mandates '{adr_title}', "
                            f"but current code reality indicates divergence: {res.reason} ({fact.get('symbol') or fact.get('path', 'unknown')}). "
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
            tech_claim = dict(tech)
            if "kind" not in tech_claim:
                tech_claim["kind"] = "technical"
            if "authority" not in tech_claim:
                tech_claim["authority"] = "code_observed"
            tech_id = tech_claim.get("id", "TECH")

            for fact in fresh_code_facts:
                fact_claim = dict(fact)
                if "kind" not in fact_claim:
                    fact_claim["kind"] = "technical"
                if "authority" not in fact_claim:
                    fact_claim["authority"] = "code_observed"
                fact_claim["is_live"] = True

                res = EpistemicAuthority.resolve_claims(tech_claim, fact_claim)
                if res.claims_conflict and res.disposition == ResolutionDisposition.RECORD_STALE:
                    fact_path = fact.get("path", "")
                    stale_records.append({
                        "type": "RECORD_STALE",
                        "disposition": ResolutionDisposition.RECORD_STALE.value,
                        "stale_id": tech_id,
                        "winner": "current_code_evidence",
                        "warning": (
                            f"STALE TECHNICAL RECORD: {tech_id} is contradicted by current code evidence at '{fact_path}'. "
                            "Fresh code observation supersedes recorded documentation."
                        ),
                        "live_evidence": fact.get("details") or fact.get("finding", ""),
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
