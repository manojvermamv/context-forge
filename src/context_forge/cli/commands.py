from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from context_forge.core.models import today, now_iso, IdentityEnvelope
from context_forge.core.identity import get_git_info
from context_forge.core.budgets import Budgets
from context_forge.core.evidence import screen_secrets
from context_forge.store.paths import brain_paths, atomic_write, read_text, read_json, STATE_DIR, BRAIN_SCHEMA_VERSION
from context_forge.store.lock import repo_lock
from context_forge.store.markdown import first_heading
from context_forge.store.registry import sync_routing_index, write_registry
from context_forge.store.sqlite_index import fts5_available, build_fts5_index
from context_forge.store.json_index import build_json_index
from context_forge.store.audit import append_audit_log
from context_forge.knowledge.candidate import pending_candidates
from context_forge.knowledge.approval import approve_candidate
from context_forge.knowledge.update import create_knowledge_record
from context_forge.knowledge.consolidate import plan_or_apply_consolidation
from context_forge.compiler.pack import compile_context_pack
from context_forge.compiler.router import search_knowledge_index
from context_forge.providers.code import build_code_map
from context_forge.traceability.sync import reconcile_sync
from context_forge.traceability.freshness import update_repository_freshness
from context_forge.cli.doctor import cmd_lint

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"


def cmd_init(repo: Path, force: bool = False) -> None:
    p = brain_paths(repo)
    with repo_lock(p):
        if p["root"].exists() and not force:
            print(f"[brain] {p['root']} already exists (use --force only to replace template-managed root files)")
        p["root"].mkdir(parents=True, exist_ok=True)
        for key in ("decisions", "concepts", "requirements", "technical", "policies", "traceability", "questions", "audit"):
            p[key].mkdir(exist_ok=True)
        p["state"].mkdir(exist_ok=True)

        defaults = {
            "current-state.md": (TEMPLATES_DIR / "current-state.md"),
            "index.md": (TEMPLATES_DIR / "index.md"),
            "overview.md": (TEMPLATES_DIR / "overview.md"),
            "status.md": (TEMPLATES_DIR / "status.md"),
            "log.md": (TEMPLATES_DIR / "log.md"),
        }
        for name, tpl in defaults.items():
            dest = p["root"] / name
            if dest.exists() and not force:
                continue
            content = read_text(tpl, f"# {name}\n\n_(template missing)_\n")
            content = content.replace("{{DATE}}", today()).replace("{{REPO}}", repo.name)
            atomic_write(dest, content)

        if not p["config"].exists():
            atomic_write(p["config"], json.dumps({
                "schema_version": BRAIN_SCHEMA_VERSION,
                "created": now_iso(),
                "repo_name": repo.name,
                "budgets": {
                    "l0_chars": Budgets.L0_CHARS,
                    "map_chars": Budgets.MAP_CHARS,
                    "topic_chars": Budgets.TOPIC_CHARS,
                },
                "decision_capture": "review",
            }, indent=2) + "\n")

        gi = p["root"] / ".gitignore"
        if not gi.exists():
            atomic_write(gi, f"{STATE_DIR}/\n*.tmp*\n")

        cmd_map(repo)
        cmd_index(repo)
        print(f"[brain] initialized {p['root']} — run the doctor: python3 brain.py doctor {repo}")


def cmd_map(repo: Path) -> None:
    p = brain_paths(repo)
    with repo_lock(p):
        content = build_code_map(repo)
        atomic_write(p["map"], content)
        status_label = "OK" if len(content) <= Budgets.MAP_CHARS * 1.1 else "over budget, see note in file"
        print(f"[brain] map.md regenerated: {len(content)} chars ({status_label})")


def cmd_index(repo: Path) -> None:
    p = brain_paths(repo)
    with repo_lock(p):
        sync_routing_index(p)
        write_registry(repo, p)
        update_repository_freshness(repo)

        docs = []
        for md in p["root"].rglob("*.md"):
            if STATE_DIR in md.parts or (p["audit"].exists() and p["audit"] in md.parents):
                continue
            try:
                size = md.stat().st_size
            except OSError:
                continue
            text = read_text(md)
            if size > Budgets.MAX_INDEX_FILE_BYTES:
                text = text[:Budgets.MAX_INDEX_FILE_BYTES]
            docs.append({"path": str(md.relative_to(repo)), "text": text, "title": first_heading(text) or md.stem})

        if fts5_available():
            build_fts5_index(p["search_index_db"], docs)
            engine = "sqlite-fts5"
        else:
            build_json_index(p["search_index_json"], docs)
            engine = "json-inverted-index"

        print(f"[brain] indexed {len(docs)} pages via {engine}")


def cmd_scan(repo: Path) -> int:
    p = brain_paths(repo)
    with repo_lock(p):
        if not p["root"].exists():
            cmd_init(repo)
        cmd_map(repo)

        commit_sha, branch, worktree = ("", "", "")
        if (repo / ".git").exists():
            commit_sha, branch, worktree = get_git_info(repo)

        map_record = p["technical"] / "codebase-map.md"
        if not map_record.exists():
            fm = [
                "---",
                "id: TECH-CODEBASE-MAP",
                "kind: technical",
                "status: observed",
                "authority: code_observed",
                f"updated: {today()}",
                f"created_at: {now_iso()}",
                "schema_version: 2.0",
                f"project_id: {repo.name}",
                f"repository: {repo.name}",
            ]
            if commit_sha:
                fm.append(f"commit_sha: {commit_sha}")
                fm.append(f"evidence_observed_commit: {commit_sha}")
            if branch:
                fm.append(f"branch: {branch}")
            fm.extend([
                "producer: scanner",
                "producer_type: tool",
                "authority_domain: IMPLEMENTATION",
                "authority_level: 70",
                "---",
                "",
                "# Codebase Map",
                "",
                "The generated [code map](../map.md) is the authoritative structural view. "
                "This record exists so any agent can route to it without treating source "
                "structure as product intent.",
                "",
                "## Evidence",
                "",
                "- Deterministic local scan of the repository.",
                "",
            ])
            atomic_write(map_record, "\n".join(fm))

        test_files = []
        from context_forge.providers.code.native import IGNORE_DIRS
        for candidate in repo.rglob("test_*.py"):
            if not any(part in IGNORE_DIRS for part in candidate.parts):
                test_files.append(candidate.relative_to(repo).as_posix())

        testing = p["technical"] / "testing.md"
        if not testing.exists():
            bullets = "\n".join(f"- `{item}`" for item in sorted(test_files)[:40]) or "- No conventional test files were detected."
            fm_t = [
                "---",
                "id: TECH-TESTING",
                "kind: technical",
                "status: observed",
                "authority: code_observed",
                f"updated: {today()}",
                f"created_at: {now_iso()}",
                "schema_version: 2.0",
                f"project_id: {repo.name}",
                f"repository: {repo.name}",
            ]
            if commit_sha:
                fm_t.append(f"commit_sha: {commit_sha}")
                fm_t.append(f"evidence_observed_commit: {commit_sha}")
            if branch:
                fm_t.append(f"branch: {branch}")
            fm_t.extend([
                "producer: scanner",
                "producer_type: tool",
                "authority_domain: IMPLEMENTATION",
                "authority_level: 70",
                "---",
                "",
                "# Testing",
                "",
                "This is a code-observed starting point, not a statement of required quality.",
                "",
                "## Detected test files",
                "",
                bullets,
                "",
            ])
            atomic_write(testing, "\n".join(fm_t))

        audit_ident = IdentityEnvelope(commit_sha=commit_sha, branch=branch, producer="scanner")
        append_audit_log(p, "scan", map_record, "Created a deterministic technical baseline; no requirements or decisions were inferred.", identity=audit_ident)
        cmd_index(repo)
        print(f"[brain] scan complete — technical baseline is available under {p['technical'].relative_to(repo)}")
        return 0


def cmd_context(
    repo: Path,
    query: str = "",
    paths: list[str] | None = None,
    pointers_only: bool = False,
    output_format: str = "text",
    explain: bool = False,
) -> None:
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return
    if not (p["search_index_db"].exists() or p["search_index_json"].exists()):
        cmd_index(repo)

    pack = compile_context_pack(repo, query, paths)

    if pointers_only:
        selected = list(pack.next_reading)
        print("Read these files, in order:")
        for item in selected:
            print(f"- {item}")
        if query and len(selected) <= 3 and not pack.authoritative_intent:
            print("- No confident routed match; use map.md before broad source exploration.")
        return pack

    if output_format == "json":
        print(json.dumps(pack.to_dict(), indent=2))
        return pack

    # Default output: rendered Markdown context pack directly usable by LLM coding agents
    rendered = pack.to_text()
    try:
        print(rendered)
    except UnicodeEncodeError:
        safe_out = rendered.encode(getattr(sys.stdout, "encoding", "utf-8") or "utf-8", errors="replace").decode(
            getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
        )
        print(safe_out)

    if explain and pack.provider_diagnostics:
        print("\n--- Provider Diagnostics Explanation ---")
        for diag in pack.provider_diagnostics:
            if isinstance(diag, dict):
                prov = diag.get("provider") or diag.get("provider_name", "provider")
                st = diag.get("status", "unknown")
                msg = diag.get("diagnostic_message") or diag.get("diagnostic", "")
                print(f"[{prov}]: status={st} · {msg}")
            else:
                print(f" - {diag}")

    return pack


def cmd_pending_review(repo: Path) -> None:
    p = brain_paths(repo)
    cands = pending_candidates(p)
    summaries = []
    for c in cands:
        delta = c.get("delta", {})
        summaries.append({
            "id": c.get("id"),
            "captured_at": c.get("captured_at"),
            "events": c.get("events", []),
            "contains_redactions": bool(c.get("contains_redactions")),
            "counts": {k: len(delta.get(k, [])) for k in ("decisions", "blockers", "next_steps")},
            "delta": delta,
        })
    print(json.dumps({
        "candidates": summaries,
        "promotion": "Inspect each candidate delta, then run review <repo> --approve <id> for exactly one approved record.",
    }, indent=2))


def run_semantic_review(repo: Path, if_due: bool) -> None:
    p = brain_paths(repo)
    marker = p["state"] / "last-review.json"
    last = json.loads(read_text(marker, "{}")) if marker.exists() else {}
    last_dt = last.get("at")

    if if_due and last_dt:
        try:
            elapsed = datetime.now(timezone.utc) - datetime.strptime(last_dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if elapsed < timedelta(days=Budgets.REVIEW_INTERVAL_DAYS):
                print(f"[brain] review skipped — last ran {elapsed.days}d ago, interval is {Budgets.REVIEW_INTERVAL_DAYS}d (deterministic skip, 0 tokens)")
                return
        except ValueError:
            pass

    llm_cmd = os.environ.get("BRAIN_LLM_CMD")
    if not llm_cmd:
        print("[brain] review requires BRAIN_LLM_CMD to be set (e.g. 'claude -p {prompt} --model claude-haiku-4-5-20251001'). Nothing sent, nothing charged.")
        return

    pages = sorted(p["root"].rglob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    pages = [f for f in pages if STATE_DIR not in f.parts][:Budgets.REVIEW_MAX_PAGES]
    corpus = "\n\n".join(f"### {f.relative_to(repo)}\n{screen_secrets(read_text(f))}" for f in pages)
    prompt = (
        "Review this project wiki for: (1) contradictions between pages, "
        "(2) stale claims a newer page supersedes, (3) important concepts mentioned "
        "but missing their own page. Return strict JSON: "
        '{"contradictions": [...], "stale": [...], "gaps": [...]}, each item a short string '
        "naming the pages involved. No prose outside the JSON.\n\n" + corpus[:40000]
    )
    try:
        argv = json.loads(llm_cmd) if llm_cmd.strip().startswith("[") else llm_cmd.split()
        argv = [a.replace("{prompt}", prompt) for a in argv]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        out = result.stdout.strip()
        start, end = out.find("{"), out.rfind("}")
        report = json.loads(out[start:end + 1]) if start != -1 else {"error": "unparseable model output"}
    except Exception as e:
        report = {"error": str(e)}

    p["reviews"].mkdir(parents=True, exist_ok=True)
    gap_queue = p["reviews"] / "semantic-review.md"
    lines = [f"\n## Review {now_iso()}"]
    for key in ("contradictions", "stale", "gaps"):
        items = report.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            lines.append(f"- ({key}) {screen_secrets(str(item))}")
    if "error" in report:
        lines.append(f"- (error) {screen_secrets(str(report['error']))}")
    with open(gap_queue, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    atomic_write(marker, json.dumps({"at": now_iso(), "pages_reviewed": len(pages)}))
    print(f"[brain] review complete over {len(pages)} pages — see {gap_queue.relative_to(repo)}")


def cmd_review(repo: Path, if_due: bool, pending: bool = False, approve: str | None = None) -> int:
    if approve:
        return approve_candidate(repo, approve)
    if pending or not if_due:
        cmd_pending_review(repo)
        return 0
    run_semantic_review(repo, if_due=True)
    return 0


def cmd_search(repo: Path, query: str) -> None:
    hits = search_knowledge_index(repo, query)
    for r in hits:
        snippet_part = f" — {r['snippet']}" if r.get("snippet") else ""
        print(f"- {r['title']}  ({r['path']}){snippet_part}")


def cmd_maintain(repo: Path, fix: bool = False) -> int:
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1
    if fix:
        cmd_map(repo)
        cmd_index(repo)

    issues = [line for line in cmd_lint(repo) if not line.startswith("clean —")]
    registry = read_json(p["registry"], {})
    records = registry.get("records", []) if isinstance(registry, dict) else []
    seen_ids: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            issues.append("registry contains an invalid record")
            continue
        path = record.get("path", "")
        if not isinstance(path, str) or not (p["root"] / path).is_file():
            issues.append(f"registry points to a missing file: {path}")
        record_id = record.get("id", "")
        if record_id:
            if record_id in seen_ids:
                issues.append(f"duplicate record id: {record_id}")
            seen_ids.add(record_id)

    p["reports"].mkdir(parents=True, exist_ok=True)
    report = p["reports"] / "maintain.json"
    atomic_write(report, json.dumps({"generated_at": now_iso(), "fixed": fix, "issues": issues}, indent=2) + "\n")

    if issues:
        print(f"[brain] maintain found {len(issues)} issue(s); see {report.relative_to(repo)}")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"[brain] maintain clean; report: {report.relative_to(repo)}")
    return 0
