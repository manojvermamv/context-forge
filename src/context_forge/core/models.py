from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class IdentityEnvelope:
    """Provenance and identity of the actor (human, agent, team, session, tool) creating or updating knowledge."""
    project_id: str = ""
    repository: str = ""
    commit_sha: str = ""
    branch: str = ""
    worktree: str = ""
    team_id: str = ""
    agent_id: str = ""
    agent_role: str = ""  # architect, developer, reviewer, tester, human_user, etc.
    session_id: str = ""
    conversation_id: str = ""
    task_id: str = ""
    producer: str = ""         # Actor or tool producing knowledge (e.g. cbm, agentmemory, user, hook, pytest)
    producer_type: str = ""    # human, agent, mcp_tool, hook, test_runner, ci
    producer_id: str = ""
    producer_version: str = ""
    reviewer: str = ""
    harness: str = ""          # antigravity, cursor, claude-code, codex, cli
    model_provider: str = ""
    model_id: str = ""
    authority_domain: str = "" # INTENT, POLICY, IMPLEMENTATION, EXPERIENCE
    authority_level: int = 70
    schema_version: str = "2.0"
    checkpoint_id: str = ""
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "IdentityEnvelope":
        if not data or not isinstance(data, dict):
            return cls()
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)



@dataclass
class EvidenceStatement:
    """Sanitized evidence statement proving the claim without secrets or raw chat dumps."""
    statement: str
    authority_level: int = 70  # 100=user_explicit, 80=code_observed, etc.
    source_type: str = "code_observed"  # user_input, code_ast, git_commit, test_result, etc.
    source_refs: list[str] = field(default_factory=list)
    contains_redactions: bool = False
    verified_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceStatement":
        if isinstance(data, str):
            return cls(statement=data)
        if not isinstance(data, dict):
            return cls(statement="Not supplied.")
        return cls(
            statement=data.get("statement", ""),
            authority_level=int(data.get("authority_level", 70)),
            source_type=data.get("source_type", "code_observed"),
            source_refs=list(data.get("source_refs", [])),
            contains_redactions=bool(data.get("contains_redactions", False)),
            verified_at=data.get("verified_at", now_iso()),
        )


@dataclass
class KnowledgeRecord:
    """Canonical model for all durable knowledge records in .brain/."""
    id: str
    kind: str  # decision, requirement, technical, question, traceability, concept
    title: str
    status: str  # accepted, observed, open, linked, deprecated, superseded, rejected
    authority: str  # user_explicit, code_observed, derived_link, unresolved, agent_inference, external_source
    updated: str = field(default_factory=today)
    body: str = ""
    evidence: EvidenceStatement = field(default_factory=lambda: EvidenceStatement(statement="Not supplied."))
    identity: IdentityEnvelope = field(default_factory=IdentityEnvelope)
    confidence: float = 1.0
    created_at: str = field(default_factory=now_iso)
    scope: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    supersedes: str = ""
    superseded_by: str = ""
    freshness: str = "fresh"  # fresh, possibly_stale, stale, contradicted, unverified
    path: str = ""  # Relative to .brain/

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["evidence"] = self.evidence.to_dict()
        res["identity"] = self.identity.to_dict()
        return res

    def to_markdown(self) -> str:
        """Serialize record into canonical Markdown with YAML frontmatter."""
        fm_lines = [
            "---",
            f"id: {self.id}",
            f"status: {self.status}",
            f"authority: {self.authority}",
            f"updated: {self.updated}",
        ]
        if self.supersedes:
            fm_lines.append(f"supersedes: {self.supersedes}")
        if self.superseded_by:
            fm_lines.append(f"superseded_by: {self.superseded_by}")
        if self.freshness != "fresh":
            fm_lines.append(f"freshness: {self.freshness}")
        if self.identity.agent_id:
            fm_lines.append(f"agent_id: {self.identity.agent_id}")
        if self.identity.commit_sha:
            fm_lines.append(f"commit_sha: {self.identity.commit_sha}")
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
    """Link connecting intent (REQ/ADR) to implementation symbols and test suites."""
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPack:
    """Compiled, token-budgeted context package compiled for a specific task."""
    task: str
    compiled_at: str = field(default_factory=now_iso)
    total_chars: int = 0
    estimated_tokens: int = 0
    budget_chars: int = 4000
    authoritative_intent: list[dict[str, Any]] = field(default_factory=list)
    current_implementation: list[dict[str, Any]] = field(default_factory=list)
    past_experience: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    conflicts_and_staleness: list[dict[str, Any]] = field(default_factory=list)
    next_reading: list[str] = field(default_factory=list)

    provider_diagnostics: list[str] = field(default_factory=list)

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
                lines.append(f"- ℹ️ {diag}")
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
