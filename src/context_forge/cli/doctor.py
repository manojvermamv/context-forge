from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from context_forge.core.budgets import Budgets
from context_forge.store.paths import brain_paths, read_text, STATE_DIR, read_json
from context_forge.hooks.inject import extract_index_table, wrap_injected_memory
from context_forge.knowledge.candidate import pending_candidates
from context_forge.providers.base import ProviderStatus
from context_forge.providers.code.cbm import CBMProvider
from context_forge.providers.experience.agentmemory import AgentMemoryProvider


def cmd_lint(repo: Path) -> list[str]:
    p = brain_paths(repo)
    problems = []
    if not p["root"].exists():
        return [f"{repo} is not initialized (run `brain.py init {repo}`)"]

    all_pages = {
        str(m.relative_to(repo)).replace("\\", "/")
        for m in p["root"].rglob("*.md")
        if STATE_DIR not in m.parts and (not p["audit"].exists() or p["audit"] not in m.parents)
    }
    linked = set()
    for source in (p["index_md"], p["overview"]):
        for destination in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", read_text(source)):
            target = repo / destination if destination.startswith(".brain/") else source.parent / destination
            try:
                linked.add(str(target.resolve().relative_to(repo.resolve())).replace("\\", "/"))
            except ValueError:
                continue

    core = {
        ".brain/current-state.md",
        ".brain/index.md",
        ".brain/overview.md",
        ".brain/status.md",
        ".brain/log.md",
        ".brain/map.md",
    }
    orphans = all_pages - linked - core
    for o in sorted(orphans):
        problems.append(f"orphan page (not referenced from index.md/overview.md): {o}")

    for folder_key in ("decisions", "concepts", "requirements", "technical", "policies", "traceability", "questions"):
        folder = p.get(folder_key)
        if not folder or not folder.exists():
            continue
        for f in folder.glob("*.md"):
            size = len(read_text(f))
            if size > Budgets.TOPIC_CHARS:
                problems.append(
                    f"oversized page ({size} > {Budgets.TOPIC_CHARS} char budget): "
                    f"{f.relative_to(repo)} — candidate for `brain.py consolidate`"
                )

    cs = read_text(p["current_state"])
    if len(cs) > Budgets.L0_CHARS:
        problems.append(f"current-state.md is {len(cs)} chars, over the {Budgets.L0_CHARS} cold-start budget")
    m = re.search(r"Last updated ([\dT:\-Z]+)", cs)
    if m:
        try:
            last = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last > timedelta(days=30):
                problems.append(f"current-state.md hasn't been touched in {(datetime.now(timezone.utc) - last).days} days — likely stale")
        except ValueError:
            pass

    if not problems:
        problems.append("clean — no structural issues found")
    return problems


def cmd_doctor(repo: Path) -> None:
    p = brain_paths(repo)
    print(f"context-forge doctor — {repo}")
    print("-" * 60)
    if not p["root"].exists():
        print("NOT INITIALIZED — run `brain.py init <repo>`")
        return

    cs = read_text(p["current_state"])
    mp = read_text(p["map"])
    canonical_pages = [page for page in p["root"].rglob("*.md") if STATE_DIR not in page.parts]
    canonical_chars = sum(len(read_text(page)) for page in canonical_pages)
    route_chars = len(extract_index_table(read_text(p["index_md"]), max_rows=8))
    cold_start_chars = len(wrap_injected_memory(
        cs + "\n" + extract_index_table(read_text(p["index_md"]), max_rows=8)
    ))

    print(Budgets.char_budget_note("current-state.md (cold-start)", len(cs), Budgets.L0_CHARS))
    print(Budgets.char_budget_note("map.md", len(mp), Budgets.MAP_CHARS))
    print(f"canonical wiki baseline: {canonical_chars} chars across {len(canonical_pages)} pages")
    print(f"cold-start payload: {cold_start_chars} chars (~{Budgets.rough_tokens(cold_start_chars)} tokens; chars/3.8 estimate)")
    if canonical_chars:
        reduction = max(0, round((1 - cold_start_chars / canonical_chars) * 100))
        print(f"progressive-disclosure reduction vs whole-wiki injection: {reduction}%")
    print(f"route table shown at cold start: {route_chars} chars; per-turn ceiling: {Budgets.TURN_MAX_POINTERS} pointers (and zero bytes when no confident match)")
    print(f"pending capture candidates: {len(pending_candidates(p))} (canonical writes require review --approve)")

    idx_db, idx_json = p["search_index_db"], p["search_index_json"]
    if idx_db.exists():
        print(f"search index: sqlite-fts5 ({idx_db.stat().st_size} bytes)")
    elif idx_json.exists():
        print(f"search index: json-inverted-index ({idx_json.stat().st_size} bytes)")
    else:
        print("search index: MISSING — run `brain.py index <repo>`")

    n_decisions = len(list(p["decisions"].glob("*.md"))) if p["decisions"].exists() else 0
    n_concepts = len(list(p["concepts"].glob("*.md"))) if p["concepts"].exists() else 0
    n_requirements = len(list(p["requirements"].rglob("*.md"))) if p["requirements"].exists() else 0
    n_technical = len(list(p["technical"].rglob("*.md"))) if p["technical"].exists() else 0
    n_policies = len(list(p["policies"].rglob("*.md"))) if p.get("policies") and p["policies"].exists() else 0
    print(f"decisions: {n_decisions} · requirements: {n_requirements} · policies: {n_policies} · technical: {n_technical} · concepts: {n_concepts} pages")

    print("\nfederated providers:")
    print("------------------------------------------------------------")
    # 1. Native Context Forge
    print("  native context forge:")
    print("    status: operational")
    print("    mode: standard-library native (zero pip runtime dependencies)")
    print("    concurrency lock: cross-process atomic file-lock active")
    print("    git-anchored freshness: enabled")

    # 2. CBM
    cbm = CBMProvider()
    cbm_configured = bool(os.environ.get("CBM_PATH") or os.environ.get("CODEBASE_MEMORY_PATH") or os.environ.get("CBM_HTTP_URL"))
    cbm_health = cbm.check_health()
    print("  codebase memory mcp (cbm) [code intelligence plane]:")
    print(f"    configured: {'yes' if cbm_configured else 'optional (not explicitly configured)'}")
    cbm_available = cbm_health.status != ProviderStatus.UNAVAILABLE
    print(f"    binary discovered: {'yes' if cbm_available else 'no'}")
    if cbm_available:
        exe = cbm._resolve_executable()
        print(f"    binary path: {exe}")
        print(f"    version: {cbm_health.version or 'unknown'}")
        print("    transport: stdio / cli")
        proj_name, proj_err = cbm.resolve_project(repo, allow_index=False)
        if proj_name:
            print(f"    indexed project: {proj_name}")
        else:
            print("    indexed project: not resolved (unindexed - native fallback active)")
        caps_str = ", ".join(cbm_health.capabilities) if cbm_health.capabilities else "default"
        print(f"    capabilities: {caps_str}")
        print(f"    status: {cbm_health.status.value}")
    else:
        if cbm_configured:
            print("    STATUS: BROKEN — explicitly configured but binary not found or unreachable")
            print(f"    diagnostic: {cbm_health.diagnostic}")
        else:
            print("    status: not installed (graceful degradation to native path-level mapping)")

    # 3. AgentMemory
    am = AgentMemoryProvider()
    am_configured = bool(os.environ.get("AGENTMEMORY_URL") or os.environ.get("AGENTMEMORY_SECRET"))
    am_health = am.check_health()
    print("  agentmemory [agent experience plane]:")
    print(f"    configured: {'yes' if am_configured else 'optional (not explicitly configured)'}")
    print(f"    endpoint url: {am.endpoint_url}")
    print(f"    auth token configured: {'yes (hidden)' if os.environ.get('AGENTMEMORY_SECRET') else 'none'}")
    if am_health.status == ProviderStatus.OK:
        print(f"    health: OK ({am_health.execution_time_ms:.1f}ms)")
        print(f"    version: {am_health.version or 'unknown'}")
        caps_str = ", ".join(am_health.capabilities) if am_health.capabilities else "default"
        print(f"    capabilities: {caps_str}")
        print("    status: operational")
    else:
        if am_configured:
            print("    STATUS: ERROR — explicitly configured but failed health check")
            print(f"    status code: {am_health.status.value}")
            print(f"    diagnostic: {am_health.diagnostic}")
        else:
            print(f"    status: not running ({am_health.status.value}) — graceful degradation to native session history")

    print("\nlint:")
    for line in cmd_lint(repo):
        print(f"  - {line}")

    llm_cmd = os.environ.get("BRAIN_LLM_CMD")
    print(f"\ngenerative pass (semantic review only): {'ENABLED via BRAIN_LLM_CMD' if llm_cmd else 'disabled (deterministic-only mode)'}")


def cmd_status(repo: Path) -> None:
    p = brain_paths(repo)
    if not p["root"].exists():
        print("not initialized")
        return
    cs_age = "?"
    m = re.search(r"Last updated ([\dT:\-Z]+)", read_text(p["current_state"]))
    if m:
        cs_age = m.group(1)
    registry = "registry" if p["registry"].exists() else "NO registry"
    indexed = (p["search_index_db"].exists() or p["search_index_json"].exists())
    print(f"initialized · current-state last updated {cs_age} · {registry} · {'indexed' if indexed else 'NOT indexed'}")
