from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from context_forge.core.budgets import Budgets


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class IdentityEnvelope:
    """Provenance and identity of the actor (human, agent, team, session, tool) creating or updating knowledge."""
    schema_version: str = "2.0"
    project_id: str = ""
    repository: str = ""               # alias: repository_id
    repository_id: str = ""
    branch: str = ""                   # alias: branch_or_ref
    branch_or_ref: str = ""
    commit_sha: str = ""               # full git commit sha (40-char)
    worktree: str = ""                 # alias: worktree_id
    worktree_id: str = ""
    task_id: str = ""
    session_id: str = ""
    conversation_id: str = ""
    checkpoint_id: str = ""
    team_id: str = ""
    agent_id: str = ""
    agent_role: str = ""               # architect, developer, reviewer, security_auditor, tester, researcher, coordinator, human_user, etc.
    producer: str = ""                 # Actor or tool producing knowledge (e.g. cbm, agentmemory, user, hook, pytest)
    producer_type: str = ""            # human, agent, mcp_tool, hook, test_runner, ci, scanner, external_doc
    producer_id: str = ""
    producer_version: str = ""
    reviewer: str = ""                 # alias: reviewer_id
    reviewer_id: str = ""
    harness: str = ""                  # antigravity, cursor, claude-code, codex, cli
    model_provider: str = ""
    model_id: str = ""
    authority_domain: str = ""         # INTENT, POLICY, IMPLEMENTATION, EXPERIENCE
    authority_level: int = 0
    created_at: str = ""               # alias: timestamp
    timestamp: str = ""
    observed_at: str = ""

    def __post_init__(self) -> None:
        # Reconcile aliases
        if self.repository and not self.repository_id:
            self.repository_id = self.repository
        elif self.repository_id and not self.repository:
            self.repository = self.repository_id

        if self.branch and not self.branch_or_ref:
            self.branch_or_ref = self.branch
        elif self.branch_or_ref and not self.branch:
            self.branch = self.branch_or_ref

        if self.worktree and not self.worktree_id:
            self.worktree_id = self.worktree
        elif self.worktree_id and not self.worktree:
            self.worktree = self.worktree_id

        if self.reviewer and not self.reviewer_id:
            self.reviewer_id = self.reviewer
        elif self.reviewer_id and not self.reviewer:
            self.reviewer = self.reviewer_id

        if self.timestamp and not self.created_at:
            self.created_at = self.timestamp
        elif self.created_at and not self.timestamp:
            self.timestamp = self.created_at

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Filter out empty string/0/None for clean serialization
        return {k: v for k, v in data.items() if v not in ("", 0, None, [], {})}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "IdentityEnvelope":
        if not data or not isinstance(data, dict):
            return cls()
        valid: dict[str, Any] = {}
        for k, v in data.items():
            if k in cls.__dataclass_fields__:
                valid[k] = v
            # Map known external alias keys
            elif k == "branch_or_ref":
                valid["branch"] = v
                valid["branch_or_ref"] = v
            elif k == "repository_id":
                valid["repository"] = v
                valid["repository_id"] = v
            elif k == "worktree_id":
                valid["worktree"] = v
                valid["worktree_id"] = v
            elif k == "reviewer_id":
                valid["reviewer"] = v
                valid["reviewer_id"] = v
            elif k == "created_at":
                valid["timestamp"] = v
                valid["created_at"] = v
        return cls(**valid)


@dataclass
class EvidenceStatement:
    """Sanitized evidence statement proving the claim without secrets or raw chat dumps."""
    statement: str = "Not supplied."
    authority_level: int = 0
    source_type: str = "code_observed"  # user_input, code_observed, code_ast, git_commit, test_result, audit_log, external_doc, agent_inference, derived_link
    source_refs: list[str] = field(default_factory=list)
    contains_redactions: bool = False
    digest: str = ""
    observed_commit: str = ""
    producer: str = ""
    verification_state: str = "unverified"  # verified, unverified, possibly_stale, stale, contradicted
    verified_at: str = ""
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, [], {})}

    @property
    def source_ref(self) -> str:
        return self.source_refs[0] if self.source_refs else ""

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceStatement":
        if isinstance(data, str):
            return cls(statement=data)
        if not isinstance(data, dict):
            return cls(statement="Not supplied.")

        refs = list(data.get("source_refs", []))
        if not refs and data.get("source_ref"):
            refs = [data["source_ref"]]

        return cls(
            statement=data.get("statement", "Not supplied."),
            authority_level=int(data.get("authority_level", 0)),
            source_type=data.get("source_type", "code_observed"),
            source_refs=refs,
            contains_redactions=bool(data.get("contains_redactions", False)),
            digest=data.get("digest", ""),
            observed_commit=data.get("observed_commit", ""),
            producer=data.get("producer", ""),
            verification_state=data.get("verification_state", "unverified"),
            verified_at=data.get("verified_at", ""),
            created_at=data.get("created_at", now_iso()),
        )



@dataclass
class ClaimProposition:
    """Structured proposition for deterministic semantic conflict resolution."""
    subject: str = ""
    predicate: str = ""
    object: str = ""
    polarity: bool = True
    claim_key: str = ""
    qualifiers: dict[str, Any] = field(default_factory=dict)
    scope: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "polarity": self.polarity,
        }
        if self.claim_key:
            d["claim_key"] = self.claim_key
        elif self.subject and self.predicate:
            d["claim_key"] = f"{self.subject}:{self.predicate}:{self.object}"
        if self.qualifiers:
            d["qualifiers"] = self.qualifiers
        if self.scope:
            d["scope"] = self.scope
        if self.confidence < 1.0:
            d["confidence"] = self.confidence
        return d

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ClaimProposition"]:
        if not data:
            return None
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return None
        subj = str(data.get("subject", "")).strip()
        pred = str(data.get("predicate", "")).strip()
        obj = str(data.get("object", "")).strip()
        if not (subj or pred or obj):
            return None
        pol_raw = data.get("polarity", True)
        if isinstance(pol_raw, str):
            pol = pol_raw.lower() not in ("false", "0", "negative", "no")
        else:
            pol = bool(pol_raw)
        return cls(
            subject=subj,
            predicate=pred,
            object=obj,
            polarity=pol,
            claim_key=str(data.get("claim_key", "")),
            qualifiers=dict(data.get("qualifiers", {})),
            scope=list(data.get("scope", [])),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass
class KnowledgeRecord:
    """Canonical model for all durable knowledge records in .brain/."""
    id: str
    kind: str  # decision, requirement, technical, question, traceability, concept, policy, invariant
    title: str
    status: str  # accepted, observed, open, linked, deprecated, superseded, rejected
    authority: str  # user_explicit, policy_mandate, code_observed, derived_link, unresolved, agent_inference, external_source
    updated: str = field(default_factory=today)
    body: str = ""
    evidence: EvidenceStatement = field(default_factory=lambda: EvidenceStatement(statement="Not supplied."))
    identity: IdentityEnvelope = field(default_factory=IdentityEnvelope)
    claim: Optional[ClaimProposition] = None
    confidence: float = 1.0
    created_at: str = field(default_factory=now_iso)
    scope: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    supersedes: str = ""
    superseded_by: str = ""
    freshness: str = "fresh"  # fresh, possibly_stale, stale, contradicted, unverified
    path: str = ""  # Relative to .brain/
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["evidence"] = self.evidence.to_dict()
        res["identity"] = self.identity.to_dict()
        res["claim"] = self.claim.to_dict() if self.claim else None
        return res

    def to_markdown(self) -> str:
        """Serialize record into canonical Markdown with YAML frontmatter."""
        fm_lines = [
            "---",
            f"id: {self.id}",
            f"kind: {self.kind}",
            f"status: {self.status}",
            f"authority: {self.authority}",
            f"updated: {self.updated or today()}",
        ]
        if self.created_at:
            fm_lines.append(f"created_at: {self.created_at}")
        if self.confidence < 1.0:
            fm_lines.append(f"confidence: {self.confidence}")
        if self.supersedes:
            fm_lines.append(f"supersedes: {self.supersedes}")
        if self.superseded_by:
            fm_lines.append(f"superseded_by: {self.superseded_by}")
        if self.freshness and self.freshness != "fresh":
            fm_lines.append(f"freshness: {self.freshness}")
        if self.claim:
            c = self.claim
            if c.subject:
                fm_lines.append(f"claim_subject: {c.subject}")
            if c.predicate:
                fm_lines.append(f"claim_predicate: {c.predicate}")
            if c.object:
                fm_lines.append(f"claim_object: {c.object}")
            fm_lines.append(f"claim_polarity: {'true' if c.polarity else 'false'}")
            if c.claim_key:
                fm_lines.append(f"claim_key: {c.claim_key}")
            if c.confidence < 1.0:
                fm_lines.append(f"claim_confidence: {c.confidence}")
            if c.scope:
                fm_lines.append("claim_scope:")
                for sc in c.scope:
                    fm_lines.append(f"  - {sc}")
            if c.qualifiers:
                fm_lines.append(f"claim_qualifiers: {json.dumps(c.qualifiers)}")

        # Provenance / Identity fields
        ident = self.identity
        if ident.schema_version:
            fm_lines.append(f"schema_version: {ident.schema_version}")
        if ident.project_id:
            fm_lines.append(f"project_id: {ident.project_id}")
        if ident.repository:
            fm_lines.append(f"repository: {ident.repository}")
        if ident.commit_sha:
            fm_lines.append(f"commit_sha: {ident.commit_sha}")
        if ident.branch:
            fm_lines.append(f"branch: {ident.branch}")
        if ident.worktree:
            fm_lines.append(f"worktree: {ident.worktree}")
        if ident.team_id:
            fm_lines.append(f"team_id: {ident.team_id}")
        if ident.agent_id:
            fm_lines.append(f"agent_id: {ident.agent_id}")
        if ident.agent_role:
            fm_lines.append(f"agent_role: {ident.agent_role}")
        if ident.session_id:
            fm_lines.append(f"session_id: {ident.session_id}")
        if ident.conversation_id:
            fm_lines.append(f"conversation_id: {ident.conversation_id}")
        if ident.task_id:
            fm_lines.append(f"task_id: {ident.task_id}")
        if ident.checkpoint_id:
            fm_lines.append(f"checkpoint_id: {ident.checkpoint_id}")
        if ident.producer:
            fm_lines.append(f"producer: {ident.producer}")
        if ident.producer_type:
            fm_lines.append(f"producer_type: {ident.producer_type}")
        if ident.producer_id:
            fm_lines.append(f"producer_id: {ident.producer_id}")
        if ident.producer_version:
            fm_lines.append(f"producer_version: {ident.producer_version}")
        if ident.reviewer:
            fm_lines.append(f"reviewer: {ident.reviewer}")
        if ident.harness:
            fm_lines.append(f"harness: {ident.harness}")
        if ident.model_provider:
            fm_lines.append(f"model_provider: {ident.model_provider}")
        if ident.model_id:
            fm_lines.append(f"model_id: {ident.model_id}")
        if ident.authority_domain:
            fm_lines.append(f"authority_domain: {ident.authority_domain}")
        if ident.authority_level:
            fm_lines.append(f"authority_level: {ident.authority_level}")
        if ident.observed_at:
            fm_lines.append(f"observed_at: {ident.observed_at}")

        # Evidence fields
        ev = self.evidence
        if ev.source_type:
            fm_lines.append(f"evidence_source_type: {ev.source_type}")
        if ev.authority_level:
            fm_lines.append(f"evidence_authority_level: {ev.authority_level}")
        if ev.observed_commit:
            fm_lines.append(f"evidence_observed_commit: {ev.observed_commit}")
        if ev.digest:
            fm_lines.append(f"evidence_digest: {ev.digest}")
        if ev.producer:
            fm_lines.append(f"evidence_producer: {ev.producer}")
        if ev.contains_redactions:
            fm_lines.append("evidence_contains_redactions: true")
        if ev.verification_state:
            fm_lines.append(f"evidence_verification_state: {ev.verification_state}")
        if ev.verified_at:
            fm_lines.append(f"evidence_verified_at: {ev.verified_at}")
        if ev.created_at:
            fm_lines.append(f"evidence_created_at: {ev.created_at}")
        if ev.source_refs:
            fm_lines.append("evidence_source_refs:")
            for sref in ev.source_refs:
                fm_lines.append(f"  - {sref}")

        # Structured lists: scope, affected_symbols, affected_tests
        if self.scope:
            fm_lines.append("scope:")
            for sc in self.scope:
                fm_lines.append(f"  - {sc}")
        if self.affected_symbols:
            fm_lines.append("affected_symbols:")
            for sym in self.affected_symbols:
                fm_lines.append(f"  - {sym}")
        if self.affected_tests:
            fm_lines.append("affected_tests:")
            for tst in self.affected_tests:
                fm_lines.append(f"  - {tst}")

        # Any extra frontmatter from previous versions/extensions
        known_keys = {
            "id", "kind", "status", "authority", "updated", "created_at", "confidence",
            "supersedes", "superseded_by", "freshness", "schema_version", "project_id",
            "repository", "commit_sha", "branch", "worktree", "team_id", "agent_id",
            "agent_role", "session_id", "conversation_id", "task_id", "checkpoint_id",
            "producer", "producer_type", "producer_id", "producer_version", "reviewer",
            "harness", "model_provider", "model_id", "authority_domain", "authority_level",
            "observed_at", "evidence_source_type", "evidence_authority_level", "evidence_observed_commit",
            "evidence_digest", "evidence_producer", "evidence_contains_redactions", "contains_redactions",
            "evidence_verification_state", "evidence_verified_at", "evidence_created_at", "evidence_observed_at",
            "evidence_source_refs", "scope", "affected_symbols", "affected_tests",
            "claim", "claim_subject", "claim_predicate", "claim_object", "claim_polarity", "claim_key",
            "claim_confidence", "claim_scope", "claim_qualifiers"
        }
        for k, v in sorted(self.extra_frontmatter.items()):
            if k not in known_keys:
                if isinstance(v, list):
                    fm_lines.append(f"{k}:")
                    for item in v:
                        fm_lines.append(f"  - {item}")
                else:
                    fm_lines.append(f"{k}: {v}")

        fm_lines.append("---")
        fm_lines.append("")
        fm_lines.append(f"# {self.title}")
        fm_lines.append("")

        heading_map = {
            "decision": "Decision",
            "requirement": "Requirement",
            "technical": "Observed behavior",
            "question": "Question",
            "concept": "Summary",
            "traceability": "Source record",
            "policy": "Policy",
            "invariant": "Invariant",
        }
        section_title = heading_map.get(self.kind, "Content")
        fm_lines.append(f"## {section_title}")
        fm_lines.append("")
        fm_lines.append(self.body or "_(none)_")
        fm_lines.append("")

        fm_lines.append("## Evidence")
        fm_lines.append("")
        fm_lines.append(self.evidence.statement or "Not supplied.")
        fm_lines.append("")

        fm_lines.append("## Related paths")
        fm_lines.append("")
        if self.scope:
            for item in self.scope:
                fm_lines.append(f"- `{item}`")
        else:
            fm_lines.append("- Not linked yet.")
        fm_lines.append("")

        return "\n".join(fm_lines)


@dataclass
class CandidateRecord:
    """Staged candidate in .brain/.state/pending/ awaiting explicit review."""
    id: str
    status: str = "pending"
    captured_at: str = field(default_factory=now_iso)
    events: list[str] = field(default_factory=list)
    session: str = ""
    source: str = "deterministic"
    contains_redactions: bool = False
    delta: dict[str, list[str]] = field(default_factory=lambda: {"decisions": [], "blockers": [], "next_steps": []})
    identity: IdentityEnvelope = field(default_factory=IdentityEnvelope)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["identity"] = self.identity.to_dict()
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateRecord":
        return cls(
            id=data.get("id", ""),
            status=data.get("status", "pending"),
            captured_at=data.get("captured_at", now_iso()),
            events=list(data.get("events", [])),
            session=data.get("session", ""),
            source=data.get("source", "deterministic"),
            contains_redactions=bool(data.get("contains_redactions", False)),
            delta=dict(data.get("delta", {})),
            identity=IdentityEnvelope.from_dict(data.get("identity")),
        )


@dataclass
class TraceabilityLink:
    """Link connecting intent (REQ/ADR/POL) to implementation symbols and test suites."""
    id: str
    source_id: str
    title: str
    paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    status: str = "linked"
    authority: str = "derived_link"
    updated: str = field(default_factory=today)
    freshness: str = "fresh"
    edge_type: str = "IMPLEMENTS"  # SATISFIES, IMPLEMENTS, VERIFIES_WITH, OBSERVES, AFFECTS_SYMBOL
    target_ref: str = ""
    producer: str = ""
    provider: str = ""
    observed_commit_sha: str = ""
    verification_state: str = "unverified"
    confidence: float = 1.0
    last_verified_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPack:
    """Compiled, token-budgeted context package compiled for a specific task."""
    task: str
    compiled_at: str = field(default_factory=now_iso)
    total_chars: int = 0
    estimated_tokens: int = 0
    budget_chars: int = Budgets.CONTEXT_PACK_CHARS
    authoritative_intent: list[dict[str, Any]] = field(default_factory=list)
    current_implementation: list[dict[str, Any]] = field(default_factory=list)
    past_experience: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    conflicts_and_staleness: list[dict[str, Any]] = field(default_factory=list)
    next_reading: list[str] = field(default_factory=list)
    provider_diagnostics: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Produce canonical JSON matching context-pack.schema.json while preserving structured diagnostics."""
        diag_items = []
        for d in self.provider_diagnostics:
            if isinstance(d, dict):
                diag_items.append(dict(d))
            else:
                diag_items.append(str(d))

        return {
            "task": self.task,
            "compiled_at": self.compiled_at,
            "total_chars": self.total_chars,
            "estimated_tokens": self.estimated_tokens,
            "budget_chars": self.budget_chars,
            "sections": {
                "authoritative_intent": self.authoritative_intent,
                "current_implementation": self.current_implementation,
                "past_experience": self.past_experience,
                "open_questions": self.open_questions,
                "conflicts_and_staleness": self.conflicts_and_staleness,
                "next_reading": self.next_reading,
                "provider_diagnostics": diag_items,
            },
            # Top-level mirrors for legacy callers
            "authoritative_intent": self.authoritative_intent,
            "current_implementation": self.current_implementation,
            "past_experience": self.past_experience,
            "open_questions": self.open_questions,
            "conflicts_and_staleness": self.conflicts_and_staleness,
            "next_reading": self.next_reading,
            "provider_diagnostics": diag_items,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPack":
        sec = data.get("sections", {})
        return cls(
            task=data.get("task", ""),
            compiled_at=data.get("compiled_at", now_iso()),
            total_chars=int(data.get("total_chars", 0)),
            estimated_tokens=int(data.get("estimated_tokens", 0)),
            budget_chars=int(data.get("budget_chars", Budgets.CONTEXT_PACK_CHARS)),
            authoritative_intent=sec.get("authoritative_intent") or data.get("authoritative_intent", []),
            current_implementation=sec.get("current_implementation") or data.get("current_implementation", []),
            past_experience=sec.get("past_experience") or data.get("past_experience", []),
            open_questions=sec.get("open_questions") or data.get("open_questions", []),
            conflicts_and_staleness=sec.get("conflicts_and_staleness") or data.get("conflicts_and_staleness", []),
            next_reading=sec.get("next_reading") or data.get("next_reading", []),
            provider_diagnostics=sec.get("provider_diagnostics") or data.get("provider_diagnostics", []),
        )

    def to_text(self) -> str:
        """Render into human and agent-readable Markdown context pack."""
        lines = [
            f"# Context Pack — Task: {self.task}",
            f"_Compiled at {self.compiled_at} · Total chars: {self.total_chars} (~{self.estimated_tokens} tokens)_",
            "",
        ]
        if self.provider_diagnostics:
            lines.append("## Provider Health & Intelligence Sources")
            for diag in self.provider_diagnostics:
                msg = diag.get("diagnostic_message") or diag.get("diagnostic") if isinstance(diag, dict) else str(diag)
                lines.append(f"- ℹ️ {msg}")
            lines.append("")

        if self.authoritative_intent:
            lines.append("## 1. Authoritative Intent (REQ / ADR)")
            for item in self.authoritative_intent:
                lines.append(f"- **[{item.get('id')}] {item.get('title')}** ({item.get('authority')})")
                if item.get("body"):
                    lines.append(f"  {item['body']}")
            lines.append("")

        if self.current_implementation:
            lines.append("## 2. Current Implementation Reality")
            for item in self.current_implementation:
                lines.append(f"- `{item.get('symbol') or item.get('path')}`: {item.get('details', '')}")
            lines.append("")

        if self.past_experience:
            lines.append("## 3. Past Experience & Lessons")
            for item in self.past_experience:
                lines.append(f"- {item.get('lesson') or item.get('finding')}")
            lines.append("")

        if self.open_questions:
            lines.append("## 4. Open Uncertainties / Questions")
            for item in self.open_questions:
                lines.append(f"- **[{item.get('id')}] {item.get('title')}**: {item.get('question')}")
            lines.append("")

        if self.conflicts_and_staleness:
            lines.append("## 5. Conflict & Staleness Alerts")
            for item in self.conflicts_and_staleness:
                lines.append(f"- ⚠️ {item.get('warning')}")
            lines.append("")

        lines.append("## 6. Next Reading (Recommended bounded files)")
        if self.next_reading:
            for f in self.next_reading:
                lines.append(f"- `{f}`")
        else:
            lines.append("- (No additional files required)")
        lines.append("")

        return "\n".join(lines)

    def to_markdown(self) -> str:
        return self.to_text()

    def render_markdown(self) -> str:
        return self.to_text()

    def finalize_budget(self, max_budget: Optional[int] = None) -> "ContextPack":
        """Deterministically finalize budget postcondition and fixed-point character metadata."""
        from context_forge.compiler.allocator import finalize_context_pack
        return finalize_context_pack(self, max_budget=max_budget)

