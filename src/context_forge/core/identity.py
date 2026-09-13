from __future__ import annotations

import os
import subprocess
from pathlib import Path
from context_forge.core.models import IdentityEnvelope, now_iso


def get_git_info(repo: Path) -> tuple[str, str, str]:
    """Return current full (commit_sha, branch, worktree) using Git without crashing if not in a repo."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD", "--abbrev-ref", "HEAD", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            sha = lines[0].strip() if len(lines) > 0 else ""
            branch = lines[1].strip() if len(lines) > 1 else ""
            toplevel = lines[2].strip() if len(lines) > 2 else ""
            return sha, branch, toplevel
    except (subprocess.SubprocessError, OSError):
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
    """Construct an IdentityEnvelope from payload, environment, and git context without false defaults."""
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

    # Agent ID resolution - unknown remains empty, no fake "default_agent"
    resolved_agent = (
        agent_id
        or p.get("agent_id")
        or p.get("agentId")
        or env.get("AGENT_ID")
        or ""
    )

    # Role resolution - unknown remains empty, no fake "developer"
    resolved_role = (
        agent_role
        or p.get("agent_role")
        or p.get("agentRole")
        or env.get("AGENT_ROLE")
        or ""
    )

    # Team ID - unknown remains empty, no fake "default_team"
    team_id = p.get("team_id") or env.get("AGENT_TEAM_ID") or ""

    # Git metadata - full 40-char SHA
    commit_sha, branch, worktree = ("", "", "")
    if repo and (repo / ".git").exists():
        commit_sha, branch, worktree = get_git_info(repo)

    # Producer separation (producer != agent: CBM, AgentMemory, hook, CI, human, etc.)
    resolved_producer = (
        producer
        or p.get("producer")
        or env.get("AGENT_PRODUCER")
        or (resolved_agent if resolved_agent else "")
    )
    resolved_producer_type = (
        producer_type
        or p.get("producer_type")
        or ("human" if resolved_role == "human_user" else ("agent" if resolved_agent else ""))
    )

    # Deterministic harness detection
    detected_harness = p.get("harness") or env.get("AGENT_HARNESS") or ""
    if not detected_harness:
        if "CLAUDE_SESSION_ID" in env or "CLAUDE_PLUGIN_ROOT" in env:
            detected_harness = "claude-code"
        elif "CODEX_THREAD_ID" in env or "CODEX_SESSION_ID" in env:
            detected_harness = "codex"
        elif "ANTIGRAVITY_AGENT" in env or "ANTIGRAVITY_APP_DATA" in env:
            detected_harness = "antigravity"
        elif "CURSOR_SESSION_ID" in env:
            detected_harness = "cursor"

    # Authority level: 0 if unknown, not arbitrary 70
    raw_auth_level = p.get("authority_level")
    auth_level = int(raw_auth_level) if raw_auth_level is not None else 0

    return IdentityEnvelope(
        schema_version="2.0",
        project_id=repo.name if repo else "",
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
        checkpoint_id=p.get("checkpoint_id") or "",
        producer=resolved_producer,
        producer_type=resolved_producer_type,
        producer_id=p.get("producer_id") or resolved_producer,
        producer_version=p.get("producer_version") or "",
        reviewer=p.get("reviewer") or env.get("AGENT_REVIEWER") or "",
        harness=detected_harness,
        model_provider=p.get("model_provider") or env.get("MODEL_PROVIDER") or "",
        model_id=p.get("model_id") or env.get("MODEL_ID") or "",
        authority_domain=p.get("authority_domain") or "",
        authority_level=auth_level,
        created_at=now_iso(),
        timestamp=now_iso(),
        observed_at=p.get("observed_at") or "",
    )
