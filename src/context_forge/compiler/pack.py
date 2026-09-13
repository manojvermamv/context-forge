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
from context_forge.providers.base import ProviderStatus, ProviderResult
from context_forge.providers.code import NativeCodeProvider, CodebaseMemoryMCPProvider
from context_forge.providers.experience import NativeExperienceProvider, AgentMemoryProvider


def compile_context_pack(
    repo: Path,
    task_query: str,
    paths: list[str] | None = None,
    budget_chars: int = Budgets.CONTEXT_PACK_CHARS,
) -> ContextPack:
    """Federate Project Truth, Code Truth, and Agent Experience into a strictly bounded task context pack."""
    p = brain_paths(repo)
    scope_paths = [sp.replace("\\", "/") for sp in (paths or [])]
    terms = " ".join([task_query, *scope_paths]).strip()

    # 1. Search knowledge index for relevant records
    hits = search_knowledge_index(repo, terms, limit=12) if terms else []

    # Augment with records directly referencing scope_paths
    hit_paths = {h["path"] for h in hits}
    if scope_paths:
        for folder in ("requirements", "decisions", "policies", "technical", "questions"):
            f_dir = p.get(folder)
            if f_dir and f_dir.exists():
                for rpath in f_dir.glob("*.md"):
                    rel_p = str(rpath.relative_to(repo)).replace("\\", "/")
                    if rel_p in hit_paths:
                        continue
                    rtext = read_text(rpath)
                    rfm, _ = extract_frontmatter_dict(rtext)
                    rscope = rfm.get("scope", [])
                    if any(sp in rscope for sp in scope_paths):
                        hits.append({"path": rel_p, "title": first_heading(rtext) or rpath.stem, "snippet": ""})
                        hit_paths.add(rel_p)

    authoritative: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    staleness_alerts: list[dict[str, Any]] = []
    matched_record_paths: list[str] = []
    recorded_tech: list[dict[str, Any]] = []

    for h in hits:
        fpath = repo / h["path"]
        if not fpath.is_file():
            continue
        text = read_text(fpath)
        fm, body = extract_frontmatter_dict(text)
        rec_id = str(fm.get("id", fpath.stem))
        kind = fm.get("kind") or (rec_id.split("-")[0] if "-" in rec_id else "")
        title = first_heading(text) or fpath.stem

        if kind in ("decision", "requirement", "policy", "invariant", "ADR", "REQ", "POL", "INV"):
            authoritative.append({
                "id": rec_id,
                "kind": kind,
                "title": title,
                "authority": fm.get("authority", "user_explicit"),
                "body": body[:300].strip(),
            })
            matched_record_paths.append(h["path"])
        elif kind in ("question", "Q"):
            questions.append({
                "id": rec_id,
                "kind": kind,
                "title": title,
                "question": body[:200].strip(),
            })
            matched_record_paths.append(h["path"])
        elif kind in ("technical", "TECH"):
            recorded_tech.append({
                "id": rec_id,
                "kind": kind,
                "title": title,
                "details": body[:300].strip(),
                "path": h["path"],
            })
            matched_record_paths.append(h["path"])
            if fm.get("freshness") in ("possibly_stale", "stale", "contradicted", "branch_diverged"):
                staleness_alerts.append({
                    "type": "STALE_RECORD",
                    "id": rec_id,
                    "warning": f"Record {rec_id} ({title}) is marked {fm.get('freshness')} against current repository state.",
                })

    # 2. Code Truth provider (CBM if available, otherwise Native)
    provider_diagnostics: list[dict[str, Any]] = []
    cbm = CodebaseMemoryMCPProvider()
    cbm_res = cbm.query_impact_result(repo, task_query, scope_paths)
    provider_diagnostics.append(cbm_res.to_dict())

    code_reality: list[dict[str, Any]] = []
    if (cbm_res.is_ok() or cbm_res.status == ProviderStatus.DEGRADED) and cbm_res.data:
        code_reality = list(cbm_res.data)
    else:
        # Graceful functional degradation to native code mapper
        native_code = NativeCodeProvider()
        native_res = native_code.query_impact_result(repo, task_query, scope_paths)
        code_reality = list(native_res.data or [])
        native_dict = native_res.to_dict()
        native_dict["fallback_used"] = True
        native_dict["diagnostic_message"] = "CBM unavailable; degraded to native code mapper."
        provider_diagnostics.append(native_dict)

    # Incorporate recorded technical knowledge into code reality
    code_reality.extend(recorded_tech)

    # 3. Experience provider (AgentMemory if available, otherwise Native)
    am = AgentMemoryProvider()
    am_res = am.recall_lessons_result(task_query, limit=4)
    provider_diagnostics.append(am_res.to_dict())

    past_experience: list[dict[str, Any]] = []
    if (am_res.is_ok() or am_res.status == ProviderStatus.DEGRADED) and am_res.data:
        past_experience = list(am_res.data)
    elif am_res.status == ProviderStatus.UNAUTHORIZED:
        past_experience = []
    else:
        native_exp = NativeExperienceProvider(repo)
        native_exp_res = native_exp.recall_lessons_result(task_query, limit=4)
        past_experience = list(native_exp_res.data or [])
        native_exp_dict = native_exp_res.to_dict()
        native_exp_dict["fallback_used"] = True
        native_exp_dict["diagnostic_message"] = "AgentMemory unavailable; degraded to native session log."
        provider_diagnostics.append(native_exp_dict)

    # 4. Cross-Plane Conflict & Staleness Reconciler
    all_alerts: list[dict[str, Any]] = []
    all_alerts.extend(ConflictResolver.detect_conflicts(authoritative, past_experience))
    all_alerts.extend(ConflictResolver.detect_code_drift(authoritative, code_reality))
    all_alerts.extend(ConflictResolver.detect_stale_records(recorded_tech, code_reality))
    all_alerts.extend(staleness_alerts)

    # 5. Task-specific Next Reading prioritization
    task_next_reading: list[str] = []
    # 5a. Directly specified scope paths
    for sp in scope_paths:
        if sp not in task_next_reading:
            task_next_reading.append(sp)
    # 5b. Matched canonical record paths
    for mr in matched_record_paths:
        if mr not in task_next_reading:
            task_next_reading.append(mr)
    # 5c. Code intelligence paths
    for cr in code_reality:
        p_val = cr.get("path")
        if p_val and p_val not in task_next_reading and (repo / p_val).exists():
            task_next_reading.append(p_val)
    # 5d. Baseline routing docs appended ONLY as fallback if space allows
    baseline_docs = [".brain/index.md", ".brain/status.md", ".brain/current-state.md", ".brain/map.md"]
    for bd in baseline_docs:
        if (repo / bd).exists() and bd not in task_next_reading:
            task_next_reading.append(bd)

    # 6. Budget allocation
    sections = {
        "authoritative_intent": authoritative,
        "current_implementation": code_reality,
        "past_experience": past_experience,
        "open_questions": questions,
        "conflicts_and_staleness": all_alerts,
        "next_reading": task_next_reading,
        "provider_diagnostics": provider_diagnostics,
    }
    trimmed = allocate_context_budget(sections, max_budget=budget_chars, task=task_query)

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
        provider_diagnostics=trimmed.get("provider_diagnostics", provider_diagnostics),
    )

    rendered = pack.to_text()
    pack.total_chars = len(rendered)
    pack.estimated_tokens = Budgets.rough_tokens(pack.total_chars)

    return pack
