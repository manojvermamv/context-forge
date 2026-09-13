from __future__ import annotations

from pathlib import Path
from typing import Any
from context_forge.core.models import ContextPack, now_iso
from context_forge.core.budgets import Budgets
from context_forge.store.paths import brain_paths, read_text
from context_forge.store.markdown import extract_frontmatter_dict, first_heading
from context_forge.compiler.router import search_knowledge_index
from context_forge.compiler.conflict import ConflictResolver
from context_forge.compiler.allocator import allocate_context_budget
from context_forge.providers.code import NativeCodeProvider, CodebaseMemoryMCPProvider
from context_forge.providers.experience import NativeExperienceProvider, AgentMemoryProvider


def compile_context_pack(
    repo: Path,
    task_query: str,
    paths: list[str] | None = None,
    budget_chars: int = Budgets.CONTEXT_PACK_CHARS,
) -> ContextPack:
    """Federate Project Truth, Code Truth, and Agent Experience into a bounded task context pack."""
    p = brain_paths(repo)
    scope_paths = paths or []
    terms = " ".join([task_query, *scope_paths]).strip()

    # 1. Search knowledge index for relevant records
    hits = search_knowledge_index(repo, terms, limit=8) if terms else []

    authoritative: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    staleness_alerts: list[dict[str, Any]] = []
    next_reading: list[str] = [f".brain/index.md", f".brain/current-state.md", f".brain/status.md"]

    for h in hits:
        fpath = repo / h["path"]
        if not fpath.is_file():
            continue
        text = read_text(fpath)
        fm, body = extract_frontmatter_dict(text)
        kind = fm.get("id", "").split("-")[0] if "-" in fm.get("id", "") else ""
        title = first_heading(text) or fpath.stem

        if kind in ("ADR", "REQ"):
            authoritative.append({
                "id": fm.get("id", fpath.stem),
                "title": title,
                "authority": fm.get("authority", "user_explicit"),
                "body": body[:300].strip(),
            })
            if h["path"] not in next_reading:
                next_reading.append(h["path"])
        elif kind == "Q":
            questions.append({
                "id": fm.get("id", fpath.stem),
                "title": title,
                "question": body[:200].strip(),
            })
        elif kind == "TECH":
            # Check freshness
            if fm.get("freshness") in ("possibly_stale", "stale", "contradicted"):
                staleness_alerts.append({
                    "warning": f"Record {fm.get('id')} ({title}) is marked {fm.get('freshness')} against current repository state."
                })

    # 2. Code Truth provider (CBM if available, otherwise Native)
    cbm = CodebaseMemoryMCPProvider()
    code_provider = cbm if cbm.is_available() else NativeCodeProvider()
    code_reality = code_provider.query_impact(task_query, scope_paths)

    # 3. Experience provider (AgentMemory if available, otherwise Native)
    am = AgentMemoryProvider()
    exp_provider = am if am.is_available() else NativeExperienceProvider(repo)
    past_experience = exp_provider.recall_lessons(task_query, limit=4)

    # 4. Conflict resolution
    detected_conflicts = ConflictResolver.detect_conflicts(authoritative, past_experience)
    all_alerts = staleness_alerts + detected_conflicts

    # 5. Budget allocation
    sections = {
        "authoritative_intent": authoritative,
        "current_implementation": code_reality,
        "past_experience": past_experience,
        "open_questions": questions,
        "conflicts_and_staleness": all_alerts,
        "next_reading": next_reading[:Budgets.TURN_MAX_POINTERS + 3],
    }
    trimmed = allocate_context_budget(sections, max_budget=budget_chars)

    pack = ContextPack(
        task=task_query,
        compiled_at=now_iso(),
        budget_chars=budget_chars,
        authoritative_intent=trimmed["authoritative_intent"],
        current_implementation=trimmed["current_implementation"],
        past_experience=trimmed["past_experience"],
        open_questions=trimmed["open_questions"],
        conflicts_and_staleness=trimmed["conflicts_and_staleness"],
        next_reading=trimmed["next_reading"],
    )

    rendered = pack.to_text()
    pack.total_chars = len(rendered)
    pack.estimated_tokens = Budgets.rough_tokens(pack.total_chars)

    return pack
