from __future__ import annotations

import os
import subprocess
from pathlib import Path
from context_forge.core.models import IdentityEnvelope, now_iso


def get_git_info(repo: Path) -> tuple[str, str]:
    """Return current (commit_sha, branch) using Git without crashing if not in a repo."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            sha = lines[0] if len(lines) > 0 else ""
            branch = lines[1] if len(lines) > 1 else ""
            return sha[:12], branch
    except Exception:
        pass
    return "", ""


def build_identity_envelope(
    payload: dict | None = None,
    repo: Path | None = None,
    agent_id: str = "",
    agent_role: str = "",
    task_id: str = "",
) -> IdentityEnvelope:
    """Construct an IdentityEnvelope from payload, environment, and git context."""
    p = payload or {}
    env = os.environ

    # Session ID resolution
    session_id = (
        p.get("session_id")
        or p.get("sessionId")
        or p.get("conversation_id")
        or p.get("conversationId")
        or env.get("AGENT_SESSION_ID")
        or env.get("CLAUDE_SESSION_ID")
        or ""
    )
    if isinstance(session_id, str):
        session_id = session_id.strip()

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
    commit_sha, branch = ("", "")
    if repo and (repo / ".git").exists():
        commit_sha, branch = get_git_info(repo)

    return IdentityEnvelope(
        project_id=repo.name if repo else "unknown_project",
        repository=str(repo.resolve()) if repo else "",
        commit_sha=commit_sha,
        branch=branch,
        team_id=team_id,
        agent_id=resolved_agent,
        agent_role=resolved_role,
        session_id=session_id,
        task_id=task_id or env.get("AGENT_TASK_ID") or "",
        timestamp=now_iso(),
    )
