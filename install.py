#!/usr/bin/env python3
"""Install context-forge hooks for Claude Code and/or Codex.

The engine is installed once (``~/.context-forge`` by default) and hook
settings point at it. JSON settings are reconciled: unrelated hook groups are
preserved, while prior context-forge groups are refreshed without duplicates.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_DEFAULT = Path.home() / ".context-forge"
AGENTS_MARKER_START = "<!-- BEGIN context-forge -->"
AGENTS_MARKER_END = "<!-- END context-forge -->"
DEFAULT_UNIX_SCRIPT = '"$HOME/.context-forge/scripts/brain.py"'
DEFAULT_WINDOWS_SCRIPT = "(Join-Path $HOME '.context-forge\\scripts\\brain.py')"


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _is_default_engine_dir(engine_dir: Path) -> bool:
    return _same_path(engine_dir, ENGINE_DEFAULT)


def _engine_reference(engine_dir: Path) -> str:
    return "~/.context-forge" if _is_default_engine_dir(engine_dir) else shlex.quote(str(engine_dir))


def _render_engine_references(text: str, engine_dir: Path) -> str:
    return text.replace("~/.context-forge", _engine_reference(engine_dir))


def _powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_hook_commands(snippet: dict, engine_dir: Path) -> dict:
    """Render reusable snippets to the actual selected engine directory."""
    rendered = copy.deepcopy(snippet)
    script = engine_dir / "scripts" / "brain.py"
    if _is_default_engine_dir(engine_dir):
        unix_script, windows_script = DEFAULT_UNIX_SCRIPT, DEFAULT_WINDOWS_SCRIPT
    else:
        unix_script = shlex.quote(str(script))
        windows_script = _powershell_single_quoted(str(script))

    for groups in rendered.get("hooks", {}).values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for handler in group.get("hooks", []):
                if not isinstance(handler, dict):
                    continue
                if isinstance(handler.get("command"), str):
                    handler["command"] = handler["command"].replace(DEFAULT_UNIX_SCRIPT, unix_script)
                if isinstance(handler.get("commandWindows"), str):
                    handler["commandWindows"] = handler["commandWindows"].replace(
                        DEFAULT_WINDOWS_SCRIPT, windows_script
                    )
    return rendered


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".context-forge.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def install_engine(engine_dir: Path) -> None:
    """Refresh tracked engine files without deleting unrelated local files."""
    engine_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("scripts", "templates"):
        shutil.copytree(HERE / subdir, engine_dir / subdir, dirs_exist_ok=True)
    for name in ("SKILL.md", "AGENTS.md.snippet.md"):
        _atomic_write(
            engine_dir / name,
            _render_engine_references((HERE / name).read_text(encoding="utf-8"), engine_dir),
        )
    print(f"[install] engine -> {engine_dir}")


def _normalise_command(command: str) -> str:
    return command.replace("\\", "/").casefold()


def _is_context_forge_handler(handler: object, engine_dir: Path) -> bool:
    if not isinstance(handler, dict):
        return False
    custom_marker = _normalise_command(str(engine_dir / "scripts" / "brain.py"))
    for key in ("command", "commandWindows"):
        command = handler.get(key)
        if not isinstance(command, str):
            continue
        normalized = _normalise_command(command)
        if "context-forge/scripts/brain.py" in normalized or custom_marker in normalized:
            return True
    return False


def _reconcile_event(existing_groups: list, desired_groups: list, engine_dir: Path) -> tuple[list, str]:
    """Replace owned handlers while retaining unrelated hooks exactly."""
    retained: list = []
    had_owned = False
    for group in existing_groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            retained.append(group)
            continue
        owned = [handler for handler in group["hooks"] if _is_context_forge_handler(handler, engine_dir)]
        if not owned:
            retained.append(group)
            continue
        had_owned = True
        unrelated = [handler for handler in group["hooks"] if handler not in owned]
        if unrelated:
            residual = copy.deepcopy(group)
            residual["hooks"] = unrelated
            retained.append(residual)
    reconciled = retained + copy.deepcopy(desired_groups)
    if reconciled == existing_groups:
        return reconciled, "current"
    return reconciled, "updated" if had_owned else "added"


def _read_config(target: Path) -> dict:
    if not target.exists():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)
        print(f"[install] WARNING: invalid JSON backed up to {backup}; rebuilding hooks only")
        return {}
    if not isinstance(value, dict):
        print(f"[install] WARNING: {target} has a non-object JSON root; rebuilding hooks only")
        return {}
    return value


def merge_hooks_json(target: Path, snippet: dict, engine_dir: Path) -> None:
    config = _read_config(target)
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        if hooks is not None:
            print(f"[install] WARNING: {target} has an invalid hooks value; replacing it")
        hooks = {}
        config["hooks"] = hooks

    added: list[str] = []
    updated: list[str] = []
    current: list[str] = []
    for event, desired_groups in snippet.get("hooks", {}).items():
        existing_groups = hooks.get(event, [])
        if not isinstance(existing_groups, list):
            print(f"[install] WARNING: {target} has an invalid {event} hook list; replacing it")
            existing_groups = []
        reconciled, state = _reconcile_event(existing_groups, desired_groups, engine_dir)
        hooks[event] = reconciled
        {"added": added, "updated": updated, "current": current}[state].append(event)

    _atomic_write(target, json.dumps(config, indent=2) + "\n")
    print(f"[install] {target}: added {added or '(none)'}, updated {updated or '(none)'}, "
          f"already current {current or '(none)'}")


def wire_claude_skill(skills_dir: Path, engine_dir: Path) -> None:
    skill = _render_engine_references((HERE / "SKILL.md").read_text(encoding="utf-8"), engine_dir)
    _atomic_write(skills_dir / "context-forge" / "SKILL.md", skill)
    print(f"[install] Claude Code skill -> {skills_dir / 'context-forge' / 'SKILL.md'}")


def append_agents_block(agents_md: Path, engine_dir: Path) -> None:
    body = _render_engine_references((HERE / "AGENTS.md.snippet.md").read_text(encoding="utf-8"), engine_dir)
    block = f"{AGENTS_MARKER_START}\n{body.strip()}\n{AGENTS_MARKER_END}\n"
    if agents_md.exists():
        text = agents_md.read_text(encoding="utf-8")
        pattern = re.escape(AGENTS_MARKER_START) + r".*?" + re.escape(AGENTS_MARKER_END) + r"\n?"
        text = re.sub(pattern, lambda _: block, text, flags=re.S) if AGENTS_MARKER_START in text else text.rstrip() + "\n\n" + block
    else:
        text = block
    _atomic_write(agents_md, text)
    print(f"[install] AGENTS.md block -> {agents_md}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["claude", "codex", "all"], required=True)
    parser.add_argument("--scope", choices=["global", "project"], default="global")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--engine-dir", type=Path, default=ENGINE_DEFAULT)
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    engine_dir = args.engine_dir.expanduser().resolve()
    install_engine(engine_dir)
    claude = render_hook_commands(json.loads((HERE / "hooks/claude/settings.snippet.json").read_text()), engine_dir)
    codex = render_hook_commands(json.loads((HERE / "hooks/codex/hooks.snippet.json").read_text()), engine_dir)

    if args.harness in ("claude", "all"):
        settings = Path.home() / ".claude" / "settings.json" if args.scope == "global" else repo / ".claude" / "settings.json"
        skills = Path.home() / ".claude" / "skills" if args.scope == "global" else repo / ".claude" / "skills"
        merge_hooks_json(settings, claude, engine_dir)
        wire_claude_skill(skills, engine_dir)

    if args.harness in ("codex", "all"):
        hooks_json = Path.home() / ".codex" / "hooks.json" if args.scope == "global" else repo / ".codex" / "hooks.json"
        agents_md = Path.home() / ".codex" / "AGENTS.md" if args.scope == "global" else repo / "AGENTS.md"
        merge_hooks_json(hooks_json, codex, engine_dir)
        append_agents_block(agents_md, engine_dir)
        print("[install] Codex: run `/hooks` once to review and trust every new or changed "
              "context-forge definition; untrusted hooks are skipped until then.")

    print("\n[install] Per-repository activation remains deliberate. Enroll a repository with:\n"
          f"    python3 {shlex.quote(str(engine_dir / 'scripts' / 'brain.py'))} init /path/to/repo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
