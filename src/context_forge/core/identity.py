from __future__ import annotations

import os
import subprocess
from pathlib import Path
from context_forge.core.models import IdentityEnvelope, now_iso


def get_git_info(repo: Path) -> tuple[str, str, str]:
    """Return current (commit_sha, branch, worktree) using Git without crashing if not in a repo."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD", "--abbrev-ref", "HEAD", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            sha = lines[0] if len(lines) > 0 else ""
            branch = lines[1] if len(lines) > 1 else ""
            toplevel = lines[2] if len(lines) > 2 else ""
            return sha[:12], branch, toplevel
    except Exception:
        pass
    return "", "", ""


def build_identity_envelope(
    payload: dict | None = None,
    repo: Path | None = None,
    agent_id: str = "",
    agent_role: str = "",
    task_id: str = "",
    producer: str = "",
    producer_type: str = "",
) -> IdentityEnvelope:
    """Construct an IdentityEnvelope from payload, environment, and git context."""
    p = payload or {}
    env = os.environ

    # Session ID resolution
    session_id = (
        p.get("session_id")
        or p.get("sessionId")
        or env.get("AGENT_SESSION_ID")
        or env.get("CLAUDE_SESSION_ID")
        or ""
    )
    if isinstance(session_id, str):
        session_id = session_id.strip()

    # Conversation ID resolution
    conversation_id = (
        p.get("conversation_id")
        or p.get("conversationId")
        or env.get("CONVERSATION_ID")
        or session_id
        or ""
    )

    # Agent ID resolution
    resolved_agent = (
        agent_id
        or p.get("agent_id")
        or p.get("agentId")
        or env.get("AGENT_ID")
        or "default_agent"
    )

    # Role resolution
    resolved_role = (
        agent_role
        or p.get("agent_role")
        or p.get("agentRole")
        or env.get("AGENT_ROLE")
        or "developer"
    )

    # Team ID
    team_id = p.get("team_id") or env.get("AGENT_TEAM_ID") or "default_team"

    # Git metadata
    commit_sha, branch, worktree = ("", "", "")
    if repo and (repo / ".git").exists():
        commit_sha, branch, worktree = get_git_info(repo)

    # Producer separation (producer != agent: CBM, AgentMemory, hook, CI, human, etc.)
    resolved_producer = (
        producer
        or p.get("producer")
        or env.get("AGENT_PRODUCER")
        or resolved_agent
    )
    resolved_producer_type = (
        producer_type
        or p.get("producer_type")
        or ("human" if resolved_role == "human_user" else "agent")
    )

    return IdentityEnvelope(
        project_id=repo.name if repo else "unknown_project",
        repository=str(repo.resolve()) if repo else "",
        commit_sha=p.get("commit_sha") or commit_sha,
        branch=p.get("branch") or branch,
        worktree=p.get("worktree") or worktree,
        team_id=team_id,
        agent_id=resolved_agent,
        agent_role=resolved_role,
        session_id=session_id,
        conversation_id=conversation_id,
        task_id=task_id or p.get("task_id") or env.get("AGENT_TASK_ID") or "",
        producer=resolved_producer,
        producer_type=resolved_producer_type,
        producer_id=p.get("producer_id") or resolved_producer,
        producer_version=p.get("producer_version") or "2.0.0",
        reviewer=p.get("reviewer") or env.get("AGENT_REVIEWER") or "",
        harness=p.get("harness") or env.get("AGENT_HARNESS") or "antigravity",
        model_provider=p.get("model_provider") or env.get("MODEL_PROVIDER") or "",
        model_id=p.get("model_id") or env.get("MODEL_ID") or "",
        authority_domain=p.get("authority_domain") or "",
        authority_level=int(p.get("authority_level", 70)),
        schema_version="2.0",
        checkpoint_id=p.get("checkpoint_id") or "",
        timestamp=now_iso(),
    )

