#!/usr/bin/env python3
"""
brain.py — the context-forge engine.

A single, zero-dependency (stdlib-only) CLI that implements a portable,
token-budgeted project knowledge base. Claude Code and Codex hooks are optional
accelerators; every workflow also works through Markdown, JSON, and CLI commands.

Why one file, stdlib-only: every mature prior-art implementation researched
for this project converges on the same rule — a memory engine that needs a
build step or an extra runtime is friction that gets skipped. (suwonleee/llmwiki
ships a Bun single-binary story; mindmuxai/brain.md advertises "zero-dependency
CLI"; Hindsight ships "zero pip install".) This follows the same rule with
Python instead, since Python is already required by most coding-agent tooling
and needs no compile step.

Design lineage (see README.md "References" for the full list): progressive
disclosure, local indexing, and deterministic linting follow the llm-wiki
pattern. Hook wiring, token budgets, gated review, and broad matcher/narrow
handler design are informed by llmwiki. The requirements/decisions/technical/
traceability split and index-first routing are informed by project-wiki. This
is an independent, from-scratch implementation of those patterns.

Subcommands:
  init            <repo>                 scaffold .brain/ for a repository
  scan            <repo>                 create a code-observed technical baseline
  map             <repo>                 regenerate the deterministic code map
  index           <repo>                 rebuild routes, registry, and local search index
  context         <repo> [task]          print the smallest useful reading list
  update          <repo>                 record an explicit durable knowledge item
  sync            <repo> [--apply]       reconcile changed code as observed evidence
  maintain        <repo> [--fix]         validate the portable knowledge base
  session-start                          hook: SessionStart -> additionalContext
  turn-inject                            hook: UserPromptSubmit -> pointers
  guard                                  hook: PreToolUse -> protect generated files
  capture         --event {stop,precompact,subagentstop,sessionend}
                                          hook: stage a review candidate only
  lint            <repo>                 deterministic health check (no LLM)
  review          <repo> [--pending|--approve ID|--if-due]
                                          candidate approval or gated semantic review
  consolidate     <repo> [--apply]       plan or explicitly apply log rotation
  doctor          <repo>                 full status report incl. token budgets
  status          <repo>                 one-line status
  search          <repo> <query>         ad-hoc keyword search over the wiki

Hook commands read one JSON object from stdin and write plain text or a JSON
object to stdout, per the (deliberately near-identical) Claude Code and Codex
hook contracts. See README.md for exactly which fields are shared and which
three fields actually differ between the two harnesses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Budgets. Every number here is a *character* budget, not a token budget —
# tokens vary by tokenizer, characters don't. As a rule of thumb divide by
# ~3.5-4 for a rough token estimate (used only for reporting, in doctor()).
# All are overridable via environment variable so a team can tune without
# touching code, mirroring llmwiki's LLMWIKI_* env surface.
# --------------------------------------------------------------------------

def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


L0_BUDGET_CHARS = _envint("BRAIN_L0_BUDGET", 1600)          # current-state.md cold-start cap
MAP_BUDGET_CHARS = _envint("BRAIN_MAP_BUDGET", 4000)         # code map cap
TOPIC_BUDGET_CHARS = _envint("BRAIN_TOPIC_BUDGET", 6000)     # concept/decision page cap
TURN_MAX_POINTERS = _envint("BRAIN_TURN_MAX_POINTERS", 3)    # per-turn pointer cap
REVIEW_MAX_PAGES = _envint("BRAIN_REVIEW_MAX_PAGES", 60)     # generative review input cap
REVIEW_INTERVAL_DAYS = _envint("BRAIN_REVIEW_INTERVAL_DAYS", 7)
LOG_ROTATE_DAYS = _envint("BRAIN_LOG_ROTATE_DAYS", 14)       # log entries older than this consolidate
MAX_INDEX_FILE_BYTES = _envint("BRAIN_MAX_INDEX_BYTES", 256 * 1024)  # per-file index cap
CAPTURE_TAIL_BYTES = _envint("BRAIN_CAPTURE_TAIL_BYTES", 8 * 1024)    # bounded hook-time transcript read
INJECTION_SUPPRESSION_SECONDS = _envint("BRAIN_INJECTION_SUPPRESSION_SECONDS", 15)

BRAIN_DIR = ".brain"
STATE_DIR = ".state"          # under .brain/, gitignored — regenerable
BRAIN_SCHEMA_VERSION = "2.0.0"
IGNORE_DIRS = {
    ".git", ".brain", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".turbo", "target", ".pytest_cache",
    ".mypy_cache", "vendor", ".idea", ".vscode", "coverage",
}
CODE_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".swift": "swift", ".scala": "scala", ".sh": "shell",
}
SYMBOL_PATTERNS = {
    "python": re.compile(r"^\s*(?:async\s+def|def|class)\s+(\w+)"),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"
        r"|^\s*(?:export\s+)?class\s+(\w+)"
        r"|^\s*export\s+const\s+(\w+)\s*="
    ),
    "typescript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"
        r"|^\s*(?:export\s+)?class\s+(\w+)"
        r"|^\s*(?:export\s+)?interface\s+(\w+)"
        r"|^\s*export\s+const\s+(\w+)\s*="
    ),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s+(?:struct|interface)"),
    "rust": re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)|^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"),
    "java": re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?class\s+(\w+)"),
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[\w\-\.]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                     # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),                 # GitHub PAT
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),                  # generic sk- style API key
]

UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Repo / path resolution
# --------------------------------------------------------------------------

def find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git`. Falls back to `start`.

    Lesson baked in from real deployments (suwonleee/llmwiki's Sept 2026
    postmortem): never assume the hook's cwd IS the project root, and never
    hardcode a fixed clone-relative path. Resolve it fresh, every call.
    """
    cur = start.resolve()
    for _ in range(50):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


def brain_paths(repo: Path):
    b = repo / BRAIN_DIR
    return {
        "root": b,
        "current_state": b / "current-state.md",
        "map": b / "map.md",
        "index_md": b / "index.md",
        "overview": b / "overview.md",
        "status": b / "status.md",
        "log": b / "log.md",
        "decisions": b / "decisions",
        "concepts": b / "concepts",
        "requirements": b / "requirements",
        "technical": b / "technical",
        "traceability": b / "traceability",
        "questions": b / "questions",
        "audit": b / "audit",
        "registry": b / "registry.json",
        "state": b / STATE_DIR,
        "pending": b / STATE_DIR / "pending",
        "approved": b / STATE_DIR / "approved",
        "discarded": b / STATE_DIR / "discarded",
        "reviews": b / STATE_DIR / "reviews",
        "reports": b / STATE_DIR / "reports",
        "injections": b / STATE_DIR / "injections",
        "search_index_json": b / STATE_DIR / "index.json",
        "search_index_db": b / STATE_DIR / "index.sqlite",
        "config": b / "config.json",
    }


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return default


def char_budget_note(label: str, n: int, budget: int) -> str:
    pct = int(100 * n / budget) if budget else 0
    flag = " ⚠ OVER BUDGET" if n > budget else ""
    return f"{label}: {n} chars / {budget} budget ({pct}%){flag}"


# --------------------------------------------------------------------------
# Hook I/O
# --------------------------------------------------------------------------

def read_hook_input() -> dict:
    """Malformed/empty stdin fails OPEN (returns {}), never raises. Every
    hook handler treats an empty payload as "do nothing" — a parsing hiccup
    must never be what blocks the person's actual tool call or turn."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def emit_context(event_name: str, text: str) -> None:
    """Emit additionalContext. Identical shape works for Claude Code AND
    Codex — both read hookSpecificOutput.additionalContext the same way.
    Empty text emits nothing (silence is a deliberate feature on the
    per-turn hook, not a bug: an unconfident pointer costs the person
    trust faster than it saves them a Read call)."""
    if not text:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def emit_deny(event_name: str, reason: str) -> None:
    """Same shape on both harnesses for PreToolUse."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def emit_block(reason: str) -> None:
    """Stop/PostToolUse-style continuation signal — same top-level shape
    on both harnesses."""
    print(json.dumps({"decision": "block", "reason": reason}))


# --------------------------------------------------------------------------
# Secret screening — applied to anything that might reach an LLM prompt.
# --------------------------------------------------------------------------

def screen_secrets(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def cmd_init(repo: Path, force: bool = False) -> None:
    p = brain_paths(repo)
    if p["root"].exists() and not force:
        print(f"[brain] {p['root']} already exists (use --force only to replace template-managed root files)")
    p["root"].mkdir(parents=True, exist_ok=True)
    for key in ("decisions", "concepts", "requirements", "technical", "traceability", "questions", "audit"):
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
        content = read_text(tpl, f"# {name}\n\n_(template missing — regenerate from context-forge skill)_\n")
        content = content.replace("{{DATE}}", today()).replace("{{REPO}}", repo.name)
        atomic_write(dest, content)

    if not p["config"].exists():
        atomic_write(p["config"], json.dumps({
            "schema_version": BRAIN_SCHEMA_VERSION,
            "created": now_iso(),
            "repo_name": repo.name,
            "budgets": {
                "l0_chars": L0_BUDGET_CHARS,
                "map_chars": MAP_BUDGET_CHARS,
                "topic_chars": TOPIC_BUDGET_CHARS,
            },
            "decision_capture": "review",
        }, indent=2) + "\n")

    # .gitignore the regenerable local state, keep the wiki itself tracked
    gi = p["root"] / ".gitignore"
    if not gi.exists():
        atomic_write(gi, f"{STATE_DIR}/\n*.tmp*\n")

    cmd_map(repo)
    cmd_index(repo)
    print(f"[brain] initialized {p['root']} — run the doctor: python3 {Path(__file__).name} doctor {repo}")


# --------------------------------------------------------------------------
# map — deterministic code map (NO LLM CALL). This is the primary
# token-overhead-savings mechanism for codebases: it replaces the agent's
# cold Glob/Grep/Read exploration loop with a single cheap, always-current
# pointer document, regenerated in well under a second.
# --------------------------------------------------------------------------

@dataclass
class FileInfo:
    rel: str
    lang: str
    lines: int
    symbols: list = field(default_factory=list)


def _iter_source_files(repo: Path):
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            ext = Path(fn).suffix
            if ext in CODE_EXT_LANG:
                yield Path(dirpath) / fn


def _detect_project_markers(repo: Path) -> list:
    markers = {
        "package.json": "Node.js / JavaScript", "pyproject.toml": "Python (pyproject)",
        "setup.py": "Python (setuptools)", "requirements.txt": "Python (pip)",
        "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java (Maven)",
        "build.gradle": "Java/Kotlin (Gradle)", "Gemfile": "Ruby",
        "composer.json": "PHP", "*.csproj": "C#/.NET",
    }
    found = []
    for name, label in markers.items():
        if "*" in name:
            if list(repo.glob(name)):
                found.append(label)
        elif (repo / name).exists():
            found.append(label)
    return found


def build_code_map(repo: Path) -> str:
    files = list(_iter_source_files(repo))
    by_lang: dict = {}
    top_dirs: dict = {}
    symbol_lines = []

    for f in files:
        rel = str(f.relative_to(repo))
        top = rel.split(os.sep, 1)[0]
        top_dirs[top] = top_dirs.get(top, 0) + 1
        lang = CODE_EXT_LANG.get(f.suffix, "other")
        by_lang[lang] = by_lang.get(lang, 0) + 1

        pat = SYMBOL_PATTERNS.get(lang)
        if pat is None:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = pat.match(line)
            if m:
                name = next((g for g in m.groups() if g), None)
                if name:
                    symbol_lines.append((rel, i, name))

    markers = _detect_project_markers(repo)
    lines = [
        "# Code Map",
        "",
        f"_Generated deterministically (no LLM call) at {now_iso()}. Regenerate anytime with_",
        "`brain.py map <repo>`. _This replaces cold repo exploration — read this before Glob/Grep._",
        "",
        "## Project type",
        ", ".join(markers) if markers else "(no standard marker file detected)",
        "",
        f"## Scale — {len(files)} source files across {len(top_dirs)} top-level directories",
        "",
        "| Top-level dir | source files |",
        "|---|---|",
    ]
    for d, n in sorted(top_dirs.items(), key=lambda kv: -kv[1])[:20]:
        lines.append(f"| `{d}/` | {n} |")

    lines += ["", "## Languages", "", "| Language | files |", "|---|---|"]
    for lang, n in sorted(by_lang.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {lang} | {n} |")

    # Symbol index — capped so this file itself stays inside its own budget.
    lines += ["", "## Key symbols (top-level def/class only, capped)", ""]
    budget_used = sum(len(l) + 1 for l in lines)
    per_file: dict = {}
    for rel, ln, name in symbol_lines:
        per_file.setdefault(rel, []).append((ln, name))

    remaining = MAP_BUDGET_CHARS - budget_used
    emitted_files = 0
    for rel, syms in sorted(per_file.items()):
        if remaining <= 0:
            lines.append(f"_...map budget ({MAP_BUDGET_CHARS} chars) reached — "
                         f"{len(per_file) - emitted_files} more files have symbols not shown; "
                         f"use `brain.py search <repo> <name>` instead._")
            break
        row = f"- `{rel}`: " + ", ".join(f"{name}:{ln}" for ln, name in syms[:12])
        if len(syms) > 12:
            row += f" (+{len(syms) - 12} more)"
        lines.append(row)
        remaining -= len(row)
        emitted_files += 1

    return "\n".join(lines) + "\n"


def cmd_map(repo: Path) -> None:
    p = brain_paths(repo)
    content = build_code_map(repo)
    atomic_write(p["map"], content)
    print(f"[brain] map.md regenerated: {len(content)} chars "
          f"({'OK' if len(content) <= MAP_BUDGET_CHARS * 1.1 else 'over budget, see note in file'})")


# --------------------------------------------------------------------------
# index — local, zero-dependency search over .brain/**.md (+ optional
# accelerated path via sqlite3 FTS5 when the interpreter supports it).
# No embeddings, no vector DB — this is a deliberate choice, not a
# shortcut: Karpathy's own note is explicit that an index this size
# ("~100 sources, ~hundreds of pages") does not need embedding-based RAG
# infrastructure, and suwonleee/llmwiki's measured numbers back it up
# (0.11s search on a 236-page wiki). Swap in `qmd` or a vector index later
# if a corpus outgrows this — see README.
# --------------------------------------------------------------------------

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
STOPWORDS = frozenset("""
a about after again all am an and any are as at be because been before being below
between both but by can did do does doing down during each few for from further had
has have having he her here hers herself him himself his how i if in into is it its
itself just me more most my myself no nor not now of off on once only or other our
ours ourselves out over own same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up very
was we were what when where which while who whom why will with you your yours
yourself yourselves
""".split())


def _tokenize(text: str) -> list:
    return [w.lower() for w in WORD_RE.findall(text)]


def _query_terms(text: str) -> list:
    """Tokens actually used to MATCH a search — stopwords and very short
    tokens excluded. Indexing still tokenizes everything (a full-text index
    is allowed to contain "the"); it's specifically the query side that
    must stay conservative, since turn-inject is silent-by-default and a
    stopword match is exactly how it stops being silent by accident."""
    return [w for w in _tokenize(text) if w not in STOPWORDS and len(w) >= 3]


def _fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except sqlite3.OperationalError:
        return False


ROUTE_BEGIN = "<!-- context-forge:auto-routes:begin -->"
ROUTE_END = "<!-- context-forge:auto-routes:end -->"

ROUTED_SECTIONS = (
    ("Decisions", "decisions"),
    ("Requirements", "requirements"),
    ("Technical knowledge", "technical"),
    ("Traceability", "traceability"),
    ("Open questions", "questions"),
    ("Concepts", "concepts"),
)


def _frontmatter_value(text: str, key: str) -> str:
    """Read a simple YAML-style frontmatter value without adding PyYAML."""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text[4:end])
    return match.group(1).strip().strip('"\'') if match else ""


def _record_kind(path: Path, root: Path) -> str:
    try:
        first = path.relative_to(root).parts[0]
    except ValueError:
        return "core"
    return {
        "decisions": "decision", "requirements": "requirement",
        "technical": "technical", "traceability": "traceability",
        "questions": "question", "concepts": "concept",
    }.get(first, "core")


def _write_registry(repo: Path, p: dict) -> None:
    """Generate a small machine-readable catalog from the portable Markdown wiki."""
    records = []
    for page in sorted(p["root"].rglob("*.md")):
        if STATE_DIR in page.parts:
            continue
        text = read_text(page)
        records.append({
            "path": page.relative_to(p["root"]).as_posix(),
            "id": _frontmatter_value(text, "id"),
            "kind": _record_kind(page, p["root"]),
            "title": _first_heading(text) or page.stem,
            "status": _frontmatter_value(text, "status"),
            "authority": _frontmatter_value(text, "authority"),
            "updated": _frontmatter_value(text, "updated") or _frontmatter_value(text, "date"),
        })
    payload = {
        "schema_version": BRAIN_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "root": BRAIN_DIR,
        "records": records,
    }
    atomic_write(p["registry"], json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _sync_routing_index(p: dict) -> None:
    """Refresh only the generated on-demand routes in index.md.

    Human-authored orientation text stays outside the marker pair. New ADR and
    concept pages become discoverable without asking an agent to edit a file
    that the PreToolUse guard correctly protects from ad-hoc changes.
    """
    original = read_text(p["index_md"])
    if not original:
        return

    lines = [ROUTE_BEGIN]
    for label, key in ROUTED_SECTIONS:
        lines += [f"### {label} (`{key}/`)"]
        section = p[key]
        pages = sorted(section.rglob("*.md")) if section.exists() else []
        if pages:
            for page in pages:
                title = _first_heading(read_text(page)) or page.stem
                title = title.replace("[", "\\[").replace("]", "\\]")
                lines.append(f"- [{title}]({page.relative_to(p['root']).as_posix()})")
        else:
            lines.append("_(none yet)_")
        lines.append("")
    lines.append(ROUTE_END)
    generated = "\n".join(lines)

    begin = original.find(ROUTE_BEGIN)
    end = original.find(ROUTE_END, begin + len(ROUTE_BEGIN)) if begin >= 0 else -1
    if begin >= 0 and end >= begin:
        updated = original[:begin] + generated + original[end + len(ROUTE_END):]
    else:
        updated = original.rstrip() + "\n\n## On-demand pages\n\n" + generated + "\n"
    if updated != original:
        atomic_write(p["index_md"], updated)


def cmd_index(repo: Path) -> None:
    p = brain_paths(repo)
    _sync_routing_index(p)
    _write_registry(repo, p)
    docs = []
    for md in p["root"].rglob("*.md"):
        if STATE_DIR in md.parts:
            continue
        try:
            size = md.stat().st_size
        except OSError:
            continue
        text = read_text(md)
        if size > MAX_INDEX_FILE_BYTES:
            text = text[:MAX_INDEX_FILE_BYTES]  # metadata-only beyond the cap
        docs.append({"path": str(md.relative_to(repo)), "text": text,
                      "title": _first_heading(text) or md.stem})

    if _fts5_available():
        _build_fts5_index(p["search_index_db"], docs)
        engine = "sqlite-fts5"
    else:
        _build_json_index(p["search_index_json"], docs)
        engine = "json-inverted-index"

    print(f"[brain] indexed {len(docs)} pages via {engine}")


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _build_fts5_index(db_path: Path, docs: list) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE VIRTUAL TABLE pages USING fts5(path, title, body)")
    con.executemany("INSERT INTO pages(path, title, body) VALUES (?, ?, ?)",
                     [(d["path"], d["title"], d["text"]) for d in docs])
    con.commit()
    con.close()


def _build_json_index(json_path: Path, docs: list) -> None:
    inverted: dict = {}
    titles = {}
    for d in docs:
        titles[d["path"]] = d["title"]
        seen = set()
        for tok in _tokenize(d["text"]):
            if tok in seen:
                continue
            seen.add(tok)
            inverted.setdefault(tok, []).append(d["path"])
    atomic_write(json_path, json.dumps({"titles": titles, "inverted": inverted}))


def search_index(repo: Path, query: str, limit: int = 5) -> list:
    p = brain_paths(repo)
    results = []
    query_terms = _query_terms(query)
    if not query_terms:
        return []

    if p["search_index_db"].exists():
        con = sqlite3.connect(str(p["search_index_db"]))
        try:
            terms = " OR ".join(f'"{t}"' for t in query_terms)
            rows = con.execute(
                "SELECT path, title, snippet(pages, 2, '', '', '…', 12) "
                "FROM pages WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT ?",
                (terms, limit),
            ).fetchall()
            results = [{"path": r[0], "title": r[1], "snippet": r[2]} for r in rows]
        except sqlite3.OperationalError:
            results = []
        finally:
            con.close()
    elif p["search_index_json"].exists():
        data = json.loads(read_text(p["search_index_json"], "{}"))
        inverted = data.get("inverted", {})
        titles = data.get("titles", {})
        scores: dict = {}
        for tok in query_terms:
            for path in inverted.get(tok, []):
                scores[path] = scores.get(path, 0) + 1
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        results = [{"path": path, "title": titles.get(path, path), "snippet": ""} for path, _ in ranked]
    return results


def cmd_search(repo: Path, query: str) -> None:
    for r in search_index(repo, query):
        print(f"- {r['title']}  ({r['path']})" + (f" — {r['snippet']}" if r.get("snippet") else ""))


# --------------------------------------------------------------------------
# session-start — cold-start injection. Injects current-state.md IN FULL
# (it is budgeted at the source) plus a short
# route table, never the whole wiki. This is the fixed per-session tax that
# does NOT grow as the wiki grows — the wiki can compound indefinitely
# without the cold-start cost compounding with it.
# --------------------------------------------------------------------------

MEMORY_BLOCK_BEGIN = "<!-- context-forge:begin -->"
MEMORY_BLOCK_END = "<!-- context-forge:end -->"


def _wrap_injected_memory(text: str) -> str:
    return f"{MEMORY_BLOCK_BEGIN}\n{text.strip()}\n{MEMORY_BLOCK_END}"


def _injection_marker(p: dict, payload: dict, repo: Path) -> Path:
    identity = _capture_session_identity(payload, repo)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return p["injections"] / f"cold-start-{digest}.json"


def _mark_cold_start_injection(p: dict, payload: dict, repo: Path) -> None:
    """Suppress just the matching session's first pointer suggestion.

    Codex can deliver SessionStart and UserPromptSubmit in close succession.
    The cold-start block already contains the route table, so repeating a
    pointer list then wastes context. This is deliberately best-effort: a
    stale or malformed marker simply means normal turn injection resumes.
    """
    try:
        marker = _injection_marker(p, payload, repo)
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(marker, json.dumps({"at": now_iso(), "consumed": False}) + "\n")
    except OSError:
        return


def _consume_cold_start_suppression(p: dict, payload: dict, repo: Path) -> bool:
    try:
        marker = _injection_marker(p, payload, repo)
        record = _read_json(marker, {})
        if not isinstance(record, dict) or record.get("consumed"):
            return False
        created = datetime.strptime(record.get("at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if datetime.now(UTC) - created > timedelta(seconds=INJECTION_SUPPRESSION_SECONDS):
            return False
        record["consumed"] = True
        atomic_write(marker, json.dumps(record) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def cmd_session_start() -> None:
    payload = read_hook_input()
    cwd = Path(payload.get("cwd") or os.getcwd())
    repo = find_repo_root(cwd)
    p = brain_paths(repo)
    event_name = payload.get("hook_event_name", "SessionStart")

    if not p["root"].exists():
        return  # repo never enrolled — zero injection, zero cost, by design

    source = str(payload.get("source", "startup"))
    compact_recovery = source == "compact" or event_name == "PostCompact"
    current_state = read_text(p["current_state"])
    index_md = read_text(p["index_md"])

    parts = [f"# context-forge — cold-start context ({source})", ""]
    if current_state:
        parts.append(current_state.strip())
    else:
        parts.append("_(no current-state.md yet — run `brain.py init`; inspect and approve "
                       "a staged capture candidate before it can populate hot memory.)_")

    # On compaction, keep it lighter — the model just had this context a
    # moment ago and lost it to the summarizer, not to session boundary.
    if not compact_recovery:
        route = _extract_index_table(index_md, max_rows=8)
        if route:
            parts.append("\n## Wiki pages available (open only what you need)\n")
            parts.append(route)

    text = "\n".join(parts).strip()
    if len(text) > L0_BUDGET_CHARS * 2:  # hard ceiling even if current-state.md drifted over budget
        text = text[: L0_BUDGET_CHARS * 2] + "\n\n_(truncated at hard ceiling — run `brain.py lint` " \
               "to see which page needs trimming)_"
    _mark_cold_start_injection(p, payload, repo)
    emit_context(event_name, _wrap_injected_memory(text))


def _extract_index_table(index_md: str, max_rows: int) -> str:
    lines = [l for l in index_md.splitlines() if l.strip().startswith("- ")]
    return "\n".join(lines[:max_rows])


# --------------------------------------------------------------------------
# turn-inject — UserPromptSubmit. Injects at most TURN_MAX_POINTERS page
# titles+paths matched against the prompt text, and NOTHING when there's no
# confident match. This is the per-turn tax, and it is deliberately tiny —
# a title + path is ~10-15 tokens; the agent Reads the file only if it
# actually decides to, so the read cost is paid once, only when useful,
# never speculatively on every turn.
# --------------------------------------------------------------------------

def cmd_turn_inject() -> None:
    payload = read_hook_input()
    cwd = Path(payload.get("cwd") or os.getcwd())
    repo = find_repo_root(cwd)
    p = brain_paths(repo)
    event_name = payload.get("hook_event_name", "UserPromptSubmit")
    prompt = payload.get("prompt", "")

    if not p["root"].exists() or not prompt.strip():
        return
    if _consume_cold_start_suppression(p, payload, repo):
        return

    hits = search_index(repo, prompt, limit=TURN_MAX_POINTERS)
    if not hits:
        return  # silent — see module docstring on emit_context

    lines = ["Related context-forge pages (open only if relevant):"]
    for h in hits:
        lines.append(f"- {h['title']} → `{h['path']}`")
    emit_context(event_name, _wrap_injected_memory("\n".join(lines)))


# --------------------------------------------------------------------------
# guard — PreToolUse. Protects engine-generated files from being clobbered
# by an ad hoc Edit/Write/apply_patch, and flags edits to the wiki's own
# raw-source citations. Best-effort by design (see official docs: hooks are
# "a useful guardrail, not a complete enforcement boundary") — Codex's
# apply_patch doesn't hand us a clean file_path, only a patch blob, so
# there we regex-scan the patch body instead of trusting a field.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Active PreToolUse guard. It protects engine-owned files and keeps a narrow,
# documented boundary: hooks are not an operating-system access-control layer.
# --------------------------------------------------------------------------

AUTO_MANAGED_BRAIN_FILES = frozenset({"map.md", "index.md", "current-state.md", "log.md"})
PATCH_FILE_DIRECTIVE = re.compile(r"(?mi)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$")
PATCH_MOVE_DIRECTIVE = re.compile(r"(?mi)^\*\*\* Move to:\s*(.+?)\s*$")
PATH_TOKEN = re.compile(r"(?:(?:[A-Za-z]:)?[\\/])?[\w. -]+(?:[\\/][\w. -]+)*\.md", re.I)


def _tool_input_texts(tool_input) -> list:
    """Accept the documented mapping and the observed raw-string fallback."""
    if isinstance(tool_input, str):
        return [tool_input]
    if not isinstance(tool_input, dict):
        return []

    texts = []
    for key in ("command", "patch", "file_path", "path", "input"):
        value = tool_input.get(key)
        if isinstance(value, str):
            texts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                texts.extend(_tool_input_texts(edit))
    return texts


def _normalize_path_components(path: str) -> list:
    """Return slash-neutral components without resolving an untrusted path."""
    cleaned = str(path).strip().strip("'\"").strip(chr(96)).replace("\\", "/")
    return [part for part in cleaned.split("/") if part not in ("", ".")]


def _is_auto_managed_brain_path(path: str) -> bool:
    components = _normalize_path_components(path)
    lowered = [part.lower() for part in components]
    if ".brain" not in lowered:
        return False
    position = len(lowered) - 1 - lowered[::-1].index(".brain")
    tail = lowered[position + 1:]
    if not tail:
        return False
    return tail[0] == STATE_DIR or tail[-1] in AUTO_MANAGED_BRAIN_FILES


def _patch_directive_paths(text: str) -> list:
    return PATCH_FILE_DIRECTIVE.findall(text) + PATCH_MOVE_DIRECTIVE.findall(text)


def _touched_paths_from_tool_input(tool_name: str, tool_input) -> list:
    paths = []
    for text in _tool_input_texts(tool_input):
        directive_paths = _patch_directive_paths(text)
        if directive_paths:
            paths.extend(directive_paths)
        else:
            paths.extend(PATH_TOKEN.findall(text))
    return list(dict.fromkeys(paths))


def _has_parseable_patch(tool_input) -> bool:
    return any(_patch_directive_paths(text) for text in _tool_input_texts(tool_input))


def cmd_guard() -> None:
    payload = read_hook_input()
    event_name = payload.get("hook_event_name", "PreToolUse")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    supported = {"Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch", "Bash"}
    if tool_name not in supported:
        return

    texts = _tool_input_texts(tool_input)
    if tool_name in {"Edit", "MultiEdit", "Write", "NotebookEdit"} and not texts:
        emit_deny(event_name, "context-forge could not inspect this memory-edit tool payload safely.")
        return
    if tool_name == "apply_patch" and not _has_parseable_patch(tool_input):
        emit_deny(
            event_name,
            "context-forge could not safely parse this apply_patch payload. "
            "It was blocked rather than risking a write to protected memory files.",
        )
        return

    for path in _touched_paths_from_tool_input(tool_name, tool_input):
        if _is_auto_managed_brain_path(path):
            emit_deny(
                event_name,
                f"{path} is automatically managed context-forge state. "
                "Capture candidates must be reviewed and approved through brain.py; "
                "regenerate map.md through brain.py instead of editing it directly.",
            )
            return
    # No decision means normal permission flow continues.
# capture — Stop / PreCompact / SubagentStop / SessionEnd. Hooks create only
# bounded, secret-screened review candidates under .brain/.state/pending.
# They never call an LLM or write canonical Markdown; a named, explicit
# `review --approve <id>` is the sole promotion path.
# --------------------------------------------------------------------------

DECISION_HINTS = re.compile(r"\b(decided|decision|going with|we will|chose|instead of|switched to)\b", re.I)
BLOCKER_HINTS = re.compile(r"\b(blocked|blocker|TODO|FIXME|open question|not sure|unclear)\b", re.I)
NEXT_STEP_HINTS = re.compile(r"\b(next step|next up|still need to|will add|plan to|should add|remaining)\b", re.I)


def _deterministic_delta(last_message: str) -> dict:
    """Zero-LLM fallback extraction: pull sentences that look like decisions,
    blockers, or next steps from the assistant's own final message. Coarse
    on purpose — it's a safety net, not the primary path. Checked in this
    order (decision/blocker hints take priority) so a sentence isn't double
    filed if it happens to match more than one pattern."""
    decisions, blockers, next_steps = [], [], []
    for sent in re.split(r"(?<=[.!?])\s+", last_message or ""):
        sent = sent.strip()
        if not sent:
            continue
        if DECISION_HINTS.search(sent):
            decisions.append(sent[:200])
        elif BLOCKER_HINTS.search(sent):
            blockers.append(sent[:200])
        elif NEXT_STEP_HINTS.search(sent):
            next_steps.append(sent[:200])
    return {"decisions": decisions[:5], "blockers": blockers[:5], "next_steps": next_steps[:5]}


def _refresh_current_state(p: dict, delta: dict) -> None:
    """Rewrite current-state.md from the *most recent* deltas only, hard-
    capped at L0_BUDGET_CHARS. This is what keeps the cold-start injection
    flat over time instead of growing with the project's history — history
    lives in log.md / concept pages, current-state.md is a rolling summary."""
    next_steps = delta.get("next_steps", [])
    decisions = delta.get("decisions", [])
    blockers = delta.get("blockers", [])

    body = ["# Current State", "", f"_Last updated {now_iso()} — auto-maintained, do not hand-edit._", ""]
    if decisions:
        body += ["## Recent decisions"] + [f"- {d}" for d in decisions] + [""]
    if blockers:
        body += ["## Open blockers / questions"] + [f"- {b}" for b in blockers] + [""]
    if next_steps:
        body += ["## Next steps"] + [f"- {n}" for n in next_steps] + [""]
    if not (decisions or blockers or next_steps):
        # Nothing new — keep whatever was already there rather than blanking it.
        return

    text = "\n".join(body).strip() + "\n"
    if len(text) > L0_BUDGET_CHARS:
        text = text[:L0_BUDGET_CHARS].rsplit("\n", 1)[0] + "\n\n_(trimmed to budget; full history in log.md)_\n"
    atomic_write(p["current_state"], text)


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Staged capture. Lifecycle hooks retain only a local, secret-screened
# candidate. They never call an LLM, edit canonical Markdown, reindex, or
# promote a fact. This keeps SessionEnd safe under Codex's three-second cap.
# --------------------------------------------------------------------------

INJECTED_MEMORY_BLOCK = re.compile(
    rf"(?is){re.escape(MEMORY_BLOCK_BEGIN)}.*?{re.escape(MEMORY_BLOCK_END)}"
)


def _strip_injected_memory(text: str) -> str:
    return INJECTED_MEMORY_BLOCK.sub("", text or "")


def _read_transcript_tail(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        path = Path(value)
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - CAPTURE_TAIL_BYTES))
            return handle.read(CAPTURE_TAIL_BYTES).decode("utf-8", errors="ignore")
    except (OSError, ValueError):
        return ""


def _capture_session_identity(payload: dict, repo: Path) -> str:
    for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"session:{value.strip()}"
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        return "transcript:" + hashlib.sha256(transcript_path.encode()).hexdigest()[:16]
    # Older hook payloads do not always carry a session id. A minute bucket
    # still collapses Stop/PreCompact/SessionEnd for the same close-out while
    # avoiding a permanent cross-session collision after approval.
    return f"fallback:{repo.resolve()}:{now_iso()[:16]}"


def _sanitize_capture_delta(delta: dict) -> tuple[dict, bool]:
    clean = {"decisions": [], "blockers": [], "next_steps": []}
    contains_redactions = False
    if not isinstance(delta, dict):
        return clean, contains_redactions
    for key in clean:
        values = delta.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values[:5]:
            if not isinstance(value, str):
                continue
            item = value.strip()[:200]
            if not item:
                continue
            screened = screen_secrets(item)
            contains_redactions |= screened != item
            clean[key].append(screened)
    return clean, contains_redactions


def _has_capture_content(delta: dict) -> bool:
    return any(delta.get(key) for key in ("decisions", "blockers", "next_steps"))


def _candidate_id(session: str, delta: dict) -> str:
    material = json.dumps({"session": session, "delta": delta}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _candidate_file(directory: Path, candidate_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{20}", candidate_id or ""):
        raise ValueError("invalid candidate id")
    return directory / f"{candidate_id}.json"


def _read_json(path: Path, default):
    try:
        return json.loads(read_text(path))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _stage_candidate(p: dict, event: str, session: str, delta: dict, redacted: bool) -> None:
    candidate_id = _candidate_id(session, delta)
    p["pending"].mkdir(parents=True, exist_ok=True)
    target = _candidate_file(p["pending"], candidate_id)
    if target.exists():
        # Preserve the candidate body while recording redundant lifecycle
        # signals. This makes the close-out path idempotent across hooks.
        existing = _read_json(target, {})
        events = existing.get("events", []) if isinstance(existing, dict) else []
        if event not in events:
            existing["events"] = events + [event]
            atomic_write(target, json.dumps(existing, indent=2, sort_keys=True) + "\n")
        return

    candidate = {
        "schema_version": 1,
        "id": candidate_id,
        "status": "pending",
        "captured_at": now_iso(),
        "events": [event],
        "session": session,
        "source": "deterministic",
        "contains_redactions": redacted,
        "delta": delta,
    }
    atomic_write(target, json.dumps(candidate, indent=2, sort_keys=True) + "\n")


def cmd_capture(event: str) -> None:
    """Fast, fail-open hook path: stage only, never mutate canonical memory."""
    try:
        payload = read_hook_input()
        cwd = Path(payload.get("cwd") or os.getcwd())
        repo = find_repo_root(cwd)
        p = brain_paths(repo)
        if not p["root"].exists():
            return

        message = payload.get("last_assistant_message") or payload.get("lastAssistantMessage") or ""
        if not isinstance(message, str) or not message.strip():
            message = _read_transcript_tail(payload.get("transcript_path"))
        message = _strip_injected_memory(message)
        delta, redacted = _sanitize_capture_delta(_deterministic_delta(message))
        if not _has_capture_content(delta):
            return
        _stage_candidate(p, event, _capture_session_identity(payload, repo), delta, redacted)
    except (OSError, TypeError, ValueError):
        # A capture hiccup must never block a user turn or SessionEnd.
        return


def _pending_candidates(p: dict) -> list:
    if not p["pending"].exists():
        return []
    candidates = []
    for path in sorted(p["pending"].glob("*.json")):
        candidate = _read_json(path, {})
        if isinstance(candidate, dict) and candidate.get("status") == "pending":
            candidates.append(candidate)
    return candidates


def _candidate_summary(candidate: dict) -> dict:
    delta = candidate.get("delta", {})
    return {
        "id": candidate.get("id"),
        "captured_at": candidate.get("captured_at"),
        "events": candidate.get("events", []),
        "contains_redactions": bool(candidate.get("contains_redactions")),
        "counts": {key: len(delta.get(key, [])) for key in ("decisions", "blockers", "next_steps")},
    "delta": delta,
    }


def cmd_pending_review(repo: Path) -> None:
    p = brain_paths(repo)
    print(json.dumps({
        "candidates": [_candidate_summary(item) for item in _pending_candidates(p)],
        "promotion": "Inspect each candidate delta, then run review <repo> --approve <id> for exactly one approved record.",
    }, indent=2))


def _validate_pending_candidate(candidate: dict, candidate_id: str) -> str | None:
    if not isinstance(candidate, dict) or candidate.get("status") != "pending":
        return "candidate is not pending"
    if candidate.get("id") != candidate_id:
        return "candidate id does not match its record"
    delta = candidate.get("delta")
    clean, redacted = _sanitize_capture_delta(delta)
    if clean != delta:
        return "candidate contains malformed or unsanitized content"
    if redacted or candidate.get("contains_redactions"):
        return "candidate contains redacted secret material and requires a human-authored sanitized record"
    if not _has_capture_content(delta):
        return "candidate contains no promotable facts"
    expected = _candidate_id(candidate.get("session", ""), delta)
    if expected != candidate_id:
        return "candidate integrity check failed"
    return None


def _approved_log_entry(candidate: dict) -> str:
    delta = candidate["delta"]
    candidate_id = candidate["id"]
    lines = [f"\n## [{now_iso()}] review-approved candidate:{candidate_id} | {candidate['source']}"]
    for key in ("decisions", "blockers", "next_steps"):
        for item in delta.get(key, []):
            lines.append(f"- ({key}) {item}")
    return "\n".join(lines) + "\n"


def cmd_approve_candidate(repo: Path, candidate_id: str) -> int:
    p = brain_paths(repo)
    try:
        pending_path = _candidate_file(p["pending"], candidate_id)
        approved_path = _candidate_file(p["approved"], candidate_id)
    except ValueError as exc:
        print(f"[brain] approval rejected: {exc}")
        return 1

    if not pending_path.exists():
        if approved_path.exists():
            print(f"[brain] candidate {candidate_id} was already approved; no duplicate promotion occurred")
        else:
            print(f"[brain] pending candidate {candidate_id} was not found")
        return 1

    candidate = _read_json(pending_path, {})
    problem = _validate_pending_candidate(candidate, candidate_id)
    if problem:
        print(f"[brain] approval rejected: {problem}")
        return 1

    marker = f"candidate:{candidate_id}"
    log_text = read_text(p["log"])
    if marker not in log_text:
        atomic_write(p["log"], log_text + _approved_log_entry(candidate))
    _refresh_current_state(p, candidate["delta"])
    cmd_index(repo)

    candidate["status"] = "approved"
    candidate["approved_at"] = now_iso()
    p["approved"].mkdir(parents=True, exist_ok=True)
    atomic_write(approved_path, json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    pending_path.unlink(missing_ok=True)
    print(f"[brain] approved candidate {candidate_id}; canonical hot memory and index refreshed")
    return 0


# --------------------------------------------------------------------------
# Portable knowledge base workflows. These commands deliberately separate
# user-approved intent from code-observed facts. Hooks never claim to know
# who approved a decision; only the active agent workflow can do that.
# --------------------------------------------------------------------------

def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or "record"


def _next_record_id(p: dict, prefix: str) -> str:
    highest = 0
    for page in p["root"].rglob("*.md"):
        match = re.search(rf"(?m)^id:\s*{re.escape(prefix)}-(\d+)\s*$", read_text(page))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def _clean_record_value(value: str, label: str) -> str:
    cleaned = screen_secrets(value).strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    if "[REDACTED]" in cleaned:
        raise ValueError(f"{label} appears to contain a secret; write a sanitized record instead")
    return cleaned


def _append_audit(p: dict, action: str, record: Path, detail: str) -> None:
    path = p["audit"] / f"knowledge-{today()}.md"
    if not path.exists():
        atomic_write(path, f"# Knowledge Audit — {today()}\n")
    entry = f"\n## {now_iso()} — {action}\n\n- Record: `{record.relative_to(p['root']).as_posix()}`\n- {detail}\n"
    atomic_write(path, read_text(path).rstrip() + entry)


def _write_traceability(p: dict, record_id: str, title: str, scope: list[str]) -> Path | None:
    """Create a compact, deterministic link record when durable intent names code."""
    if not scope:
        return None
    trace_id = _next_record_id(p, "TRACE")
    destination = p["traceability"] / f"{trace_id}-{_slugify(title)}.md"
    paths = "\n".join(f"- `{item.replace(chr(92), '/')}`" for item in scope)
    atomic_write(destination, "---\n"
        f"id: {trace_id}\nstatus: linked\nauthority: derived_link\nupdated: {today()}\n---\n\n"
        f"# Traceability — {title}\n\n## Source record\n\n- `{record_id}`\n\n"
        f"## Related paths\n\n{paths}\n")
    return destination


def cmd_scan(repo: Path) -> int:
    """Create a conservative first technical baseline from the codebase.

    It records only facts that can be observed locally; it never invents
    requirements or decisions from the current implementation.
    """
    p = brain_paths(repo)
    if not p["root"].exists():
        cmd_init(repo)
    cmd_map(repo)
    map_record = p["technical"] / "codebase-map.md"
    if not map_record.exists():
        atomic_write(map_record, "---\n"
            "id: TECH-CODEBASE-MAP\nstatus: observed\nauthority: code_observed\n"
            f"updated: {today()}\n---\n\n# Codebase Map\n\n"
            "The generated [code map](../map.md) is the authoritative structural view. "
            "This record exists so any agent can route to it without treating source "
            "structure as product intent.\n\n## Evidence\n\n- Deterministic local scan of the repository.\n")
    test_files = []
    for candidate in repo.rglob("test_*.py"):
        if not any(part in IGNORE_DIRS for part in candidate.parts):
            test_files.append(candidate.relative_to(repo).as_posix())
    testing = p["technical"] / "testing.md"
    if not testing.exists():
        bullets = "\n".join(f"- `{item}`" for item in sorted(test_files)[:40]) or "- No conventional test files were detected."
        atomic_write(testing, "---\n"
            "id: TECH-TESTING\nstatus: observed\nauthority: code_observed\n"
            f"updated: {today()}\n---\n\n# Testing\n\n"
            "This is a code-observed starting point, not a statement of required quality.\n\n"
            "## Detected test files\n\n" + bullets + "\n")
    _append_audit(p, "scan", map_record, "Created a deterministic technical baseline; no requirements or decisions were inferred.")
    cmd_index(repo)
    print(f"[brain] scan complete — technical baseline is available under {p['technical'].relative_to(repo)}")
    return 0


def cmd_context(repo: Path, query: str = "", paths: list[str] | None = None) -> None:
    """Give any agent a bounded, copyable reading list without requiring hooks."""
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return
    if not (p["search_index_db"].exists() or p["search_index_json"].exists()):
        cmd_index(repo)
    selected = [f"{BRAIN_DIR}/index.md", f"{BRAIN_DIR}/current-state.md", f"{BRAIN_DIR}/status.md"]
    terms = " ".join([query, *(paths or [])]).strip()
    for hit in search_index(repo, terms, limit=TURN_MAX_POINTERS) if terms else []:
        if hit["path"] not in selected:
            selected.append(hit["path"])
    print("Read these files, in order:")
    for item in selected:
        print(f"- {item}")
    if terms and len(selected) == 3:
        print("- No confident routed match; use map.md before broad source exploration.")


def cmd_update(
    repo: Path, kind: str, title: str, body: str, authority: str,
    evidence: str, scope: list[str], accept: bool,
) -> int:
    """Write one deliberate knowledge record with provenance.

    Accepted requirements and decisions are allowed only when an active agent
    has explicit user evidence. This is intentionally not callable by hooks.
    """
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1
    try:
        title = _clean_record_value(title, "title")
        body = _clean_record_value(body, "body")
        evidence = _clean_record_value(evidence, "evidence") if evidence else "Not supplied."
    except ValueError as exc:
        print(f"[brain] update rejected: {exc}")
        return 1

    policy = {
        "decision": ("ADR", "decisions", "accepted"),
        "requirement": ("REQ", "requirements", "accepted"),
        "technical": ("TECH", "technical", "observed"),
        "question": ("Q", "questions", "open"),
    }
    prefix, section, status = policy[kind]
    if kind in ("decision", "requirement") and (authority != "user_explicit" or not accept):
        print("[brain] update rejected: accepted intent requires --authority user_explicit, "
              "--evidence, and --accept. Record ambiguity as a question instead.")
        return 1
    if kind == "technical" and authority != "code_observed":
        print("[brain] update rejected: technical records must use --authority code_observed")
        return 1
    if kind == "question" and authority not in ("unresolved", "agent_inference", "user_explicit"):
        print("[brain] update rejected: questions must remain unresolved or cite explicit user context")
        return 1

    record_id = _next_record_id(p, prefix)
    path = p[section] / f"{record_id}-{_slugify(title)}.md"
    normalized_scope = [item.replace("\\", "/") for item in scope]
    scope_lines = "\n".join(f"- `{item}`" for item in normalized_scope) or "- Not linked yet."
    heading = {"decision": "Decision", "requirement": "Requirement", "technical": "Observed behavior", "question": "Question"}[kind]
    text = (
        "---\n"
        f"id: {record_id}\nstatus: {status}\nauthority: {authority}\n"
        f"updated: {today()}\n---\n\n# {title}\n\n"
        f"## {heading}\n\n{body}\n\n## Evidence\n\n{evidence}\n\n"
        f"## Related paths\n\n{scope_lines}\n"
    )
    atomic_write(path, text)
    _append_audit(p, f"record {kind}", path, f"Authority: `{authority}`; status: `{status}`.")
    trace = _write_traceability(p, record_id, title, scope) if kind in ("decision", "requirement") else None
    if trace:
        _append_audit(p, "traceability", trace, f"Linked `{record_id}` to {len(scope)} path(s).")
    cmd_index(repo)
    print(f"[brain] recorded {record_id} at {path.relative_to(repo)}")
    return 0


def _git_changed_paths(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], text=True,
        capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        return []
    paths = []
    for line in result.stdout.splitlines():
        if len(line) >= 4:
            paths.append(line[3:].split(" -> ")[-1].replace("\\", "/"))
    return sorted(set(paths))


def cmd_sync(repo: Path, explicit_paths: list[str], apply: bool) -> int:
    """Reconcile manual/external source changes as code-observed evidence."""
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1
    changed = sorted(set(explicit_paths or _git_changed_paths(repo)))
    if not changed:
        print("[brain] sync found no changed paths; pass --path for files changed outside Git.")
        return 0
    report = p["reports"] / f"sync-{today()}.json"
    p["reports"].mkdir(parents=True, exist_ok=True)
    atomic_write(report, json.dumps({"generated_at": now_iso(), "paths": changed}, indent=2) + "\n")
    if not apply:
        print(f"[brain] sync plan: {len(changed)} changed paths; inspect {report.relative_to(repo)} then rerun with --apply")
        return 0
    record_id = _next_record_id(p, "TECH")
    destination = p["technical"] / f"{record_id}-observed-changes-{today()}.md"
    lines = "\n".join(f"- `{item}`" for item in changed)
    atomic_write(destination, "---\n"
        f"id: {record_id}\nstatus: observed\nauthority: code_observed\nupdated: {today()}\n---\n\n"
        f"# Observed Changes — {today()}\n\n"
        "These paths changed outside the current agent workflow. This record documents observed "
        "state only; it does not assert product intent or approve a decision.\n\n"
        f"## Related paths\n\n{lines}\n\n## Evidence\n\n- `{report.relative_to(repo).as_posix()}`\n")
    _append_audit(p, "sync", destination, f"Recorded {len(changed)} externally changed paths as technical evidence.")
    cmd_map(repo)
    cmd_index(repo)
    print(f"[brain] sync applied — recorded {len(changed)} changed paths as code-observed evidence")
    return 0


def cmd_maintain(repo: Path, fix: bool = False) -> int:
    """Run deterministic health checks; never makes semantic claims."""
    p = brain_paths(repo)
    if not p["root"].exists():
        print(f"[brain] {repo} is not initialized — run `brain.py init {repo}`")
        return 1
    if fix:
        cmd_map(repo)
        cmd_index(repo)
    issues = [line for line in cmd_lint(repo) if not line.startswith("clean —")]
    registry = _read_json(p["registry"], {})
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

# lint — deterministic, zero LLM. Run it on demand after deliberate wiki edits.
# --------------------------------------------------------------------------

def cmd_lint(repo: Path) -> list:
    p = brain_paths(repo)
    problems = []
    if not p["root"].exists():
        return [f"{repo} is not initialized (run `brain.py init {repo}`)"]

    all_pages = {
        str(m.relative_to(repo)).replace("\\", "/") for m in p["root"].rglob("*.md")
        if STATE_DIR not in m.parts and p["audit"] not in m.parents
    }
    linked = set()
    for source in (p["index_md"], p["overview"]):
        for destination in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", read_text(source)):
            target = repo / destination if destination.startswith(f"{BRAIN_DIR}/") else source.parent / destination
            try:
                linked.add(str(target.resolve().relative_to(repo.resolve())).replace("\\", "/"))
            except ValueError:
                # A link outside this repository is not a context-forge route.
                continue

    core = {f"{BRAIN_DIR}/current-state.md", f"{BRAIN_DIR}/index.md",
            f"{BRAIN_DIR}/overview.md", f"{BRAIN_DIR}/status.md", f"{BRAIN_DIR}/log.md", f"{BRAIN_DIR}/map.md"}
    orphans = all_pages - linked - core
    for o in sorted(orphans):
        problems.append(f"orphan page (not referenced from index.md/overview.md): {o}")

    for page in (p["decisions"], p["concepts"], p["requirements"], p["technical"], p["traceability"], p["questions"]):
        if not page.exists():
            continue
        for f in page.glob("*.md"):
            size = len(read_text(f))
            if size > TOPIC_BUDGET_CHARS:
                problems.append(f"oversized page ({size} > {TOPIC_BUDGET_CHARS} char budget): "
                                 f"{f.relative_to(repo)} — candidate for `brain.py consolidate`")

    cs = read_text(p["current_state"])
    if len(cs) > L0_BUDGET_CHARS:
        problems.append(f"current-state.md is {len(cs)} chars, over the {L0_BUDGET_CHARS} cold-start budget")
    m = re.search(r"Last updated ([\dT:\-Z]+)", cs)
    if m:
        try:
            last = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            if datetime.now(UTC) - last > timedelta(days=30):
                problems.append(f"current-state.md hasn't been touched in {(datetime.now(UTC) - last).days} days "
                                 f"— likely stale")
        except ValueError:
            pass

    if not problems:
        problems.append("clean — no structural issues found")
    return problems


# --------------------------------------------------------------------------
# review — GENERATIVE, gated. Costs tokens, so it is off by default and
# rate-limited by REVIEW_INTERVAL_DAYS even when requested with --if-due.
# Scoped to at most REVIEW_MAX_PAGES pages to bound the prompt itself.
# --------------------------------------------------------------------------

def _run_semantic_review(repo: Path, if_due: bool) -> None:
    p = brain_paths(repo)
    marker = p["state"] / "last-review.json"
    last = json.loads(read_text(marker, "{}")) if marker.exists() else {}
    last_dt = last.get("at")

    if if_due and last_dt:
        try:
            elapsed = datetime.now(UTC) - datetime.strptime(last_dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            if elapsed < timedelta(days=REVIEW_INTERVAL_DAYS):
                print(f"[brain] review skipped — last ran {elapsed.days}d ago, "
                      f"interval is {REVIEW_INTERVAL_DAYS}d (deterministic skip, 0 tokens)")
                return
        except ValueError:
            pass

    llm_cmd = os.environ.get("BRAIN_LLM_CMD")
    if not llm_cmd:
        print("[brain] review requires BRAIN_LLM_CMD to be set (e.g. "
              "'claude -p {prompt} --model claude-haiku-4-5-20251001'). "
              "Nothing sent, nothing charged.")
        return

    pages = sorted(p["root"].rglob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    pages = [f for f in pages if STATE_DIR not in f.parts][:REVIEW_MAX_PAGES]
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


# --------------------------------------------------------------------------

def cmd_review(repo: Path, if_due: bool, pending: bool = False, approve: str | None = None) -> int:
    """Review pending capture candidates or run an opt-in semantic health check.

    The no-flag path is intentionally read-only. Promotion requires a
    specific candidate id, making the human review step explicit and auditable.
    """
    if approve:
        return cmd_approve_candidate(repo, approve)
    if pending or not if_due:
        cmd_pending_review(repo)
        return 0
    _run_semantic_review(repo, if_due=True)
    return 0

# consolidate — plan (or explicitly apply) rotation of aged approved log
# entries into a generated concept page so the working set stays bounded.
# --------------------------------------------------------------------------

def cmd_consolidate(repo: Path, apply: bool = False) -> int:
    p = brain_paths(repo)
    log_text = read_text(p["log"])
    cutoff = datetime.now(UTC) - timedelta(days=LOG_ROTATE_DAYS)
    aged, kept = [], []
    for block in re.split(r"(?=\n## \[)", log_text):
        m = re.match(r"\n?## \[([\dT:\-Z]+)\]", block)
        if not m:
            kept.append(block)
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            kept.append(block)
            continue
        (aged if ts < cutoff else kept).append(block)

    if not aged:
        print("[brain] nothing older than the rotation window — nothing to consolidate")
        return 0

    if not apply:
        print(f"[brain] consolidation plan: {len(aged)} aged log entries would move to "
              f"{(p['concepts'] / f'log-summary-{today()}.md').relative_to(repo)}. "
              "Review it, then rerun with --apply to make this canonical-memory write.")
        return 0

    summary_path = p["concepts"] / f"log-summary-{today()}.md"
    header = (f"# Log summary ({len(aged)} entries older than {LOG_ROTATE_DAYS}d, "
              f"consolidated {now_iso()})\n\n_Raw entries removed from log.md after this point; "
              f"this page is the durable record._\n\n")
    atomic_write(summary_path, header + "".join(aged))
    atomic_write(p["log"], "".join(kept) or "# log.md\n\n_(rotated — see concepts/log-summary-*.md for history)_\n")
    cmd_index(repo)
    print(f"[brain] consolidated {len(aged)} aged entries into {summary_path.relative_to(repo)}")
    return 0


# --------------------------------------------------------------------------
# doctor / status
# --------------------------------------------------------------------------

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
    route_chars = len(_extract_index_table(read_text(p["index_md"]), max_rows=8))
    cold_start_chars = len(_wrap_injected_memory(
        cs + "\n" + _extract_index_table(read_text(p["index_md"]), max_rows=8)
    ))
    print(char_budget_note("current-state.md (cold-start)", len(cs), L0_BUDGET_CHARS))
    print(char_budget_note("map.md", len(mp), MAP_BUDGET_CHARS))
    print(f"canonical wiki baseline: {canonical_chars} chars across {len(canonical_pages)} pages")
    print(f"cold-start payload: {cold_start_chars} chars (~{round(cold_start_chars / 3.8)} tokens; chars/3.8 estimate)")
    if canonical_chars:
        reduction = max(0, round((1 - cold_start_chars / canonical_chars) * 100))
        print(f"progressive-disclosure reduction vs whole-wiki injection: {reduction}%")
    print(f"route table shown at cold start: {route_chars} chars; per-turn ceiling: "
          f"{TURN_MAX_POINTERS} pointers (and zero bytes when no confident match)")
    print(f"pending capture candidates: {len(_pending_candidates(p))} (canonical writes require review --approve)")

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
    print(f"decisions: {n_decisions} · requirements: {n_requirements} · technical: {n_technical} · concepts: {n_concepts} pages")

    print("\nlint:")
    for line in cmd_lint(repo):
        print(f"  - {line}")

    llm_cmd = os.environ.get("BRAIN_LLM_CMD")
    print(f"\ngenerative pass (semantic review only): "
          f"{'ENABLED via BRAIN_LLM_CMD' if llm_cmd else 'disabled (deterministic-only mode)'}")


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
    print(f"initialized · current-state last updated {cs_age} · {registry} · "
          f"{'indexed' if (p['search_index_db'].exists() or p['search_index_json'].exists()) else 'NOT indexed'}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="brain.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("repo", type=Path)
    p_init.add_argument("--force", action="store_true")

    p_map = sub.add_parser("map")
    p_map.add_argument("repo", type=Path)

    p_index = sub.add_parser("index")
    p_index.add_argument("repo", type=Path)

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("repo", type=Path)

    p_context = sub.add_parser("context")
    p_context.add_argument("repo", type=Path)
    p_context.add_argument("query", nargs="*", help="task words for a routed reading list")
    p_context.add_argument("--path", dest="paths", action="append", default=[], help="affected source path")

    p_update = sub.add_parser("update")
    p_update.add_argument("repo", type=Path)
    p_update.add_argument("--kind", choices=["decision", "requirement", "technical", "question"], required=True)
    p_update.add_argument("--title", required=True)
    p_update.add_argument("--body", required=True)
    p_update.add_argument("--authority", required=True,
                          choices=["user_explicit", "code_observed", "unresolved", "agent_inference", "external_source"])
    p_update.add_argument("--evidence", default="")
    p_update.add_argument("--scope", action="append", default=[])
    p_update.add_argument("--accept", action="store_true", help="publish explicit user-approved intent")

    p_sync = sub.add_parser("sync")
    p_sync.add_argument("repo", type=Path)
    p_sync.add_argument("--path", dest="paths", action="append", default=[], help="changed path when Git cannot report it")
    p_sync.add_argument("--apply", action="store_true", help="write a code-observed technical record")

    p_maintain = sub.add_parser("maintain")
    p_maintain.add_argument("repo", type=Path)
    p_maintain.add_argument("--fix", action="store_true", help="regenerate map, routes, and registry before validation")

    sub.add_parser("session-start")
    sub.add_parser("turn-inject")
    sub.add_parser("guard")

    p_capture = sub.add_parser("capture")
    p_capture.add_argument("--event", choices=["stop", "precompact", "subagentstop", "sessionend"], required=True)

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("repo", type=Path)

    p_review = sub.add_parser("review")
    p_review.add_argument("repo", type=Path)
    p_review.add_argument("--if-due", action="store_true")
    p_review.add_argument("--pending", action="store_true", help="list staged capture candidates")
    p_review.add_argument("--approve", metavar="CANDIDATE_ID", help="explicitly promote one reviewed candidate")

    p_consolidate = sub.add_parser("consolidate")
    p_consolidate.add_argument("repo", type=Path)
    p_consolidate.add_argument("--apply", action="store_true", help="explicitly apply a reviewed consolidation plan")

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("repo", type=Path)

    p_status = sub.add_parser("status")
    p_status.add_argument("repo", type=Path)

    p_search = sub.add_parser("search")
    p_search.add_argument("repo", type=Path)
    p_search.add_argument("query")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        cmd_init(args.repo, args.force)
    elif args.cmd == "scan":
        return cmd_scan(args.repo)
    elif args.cmd == "map":
        cmd_map(args.repo)
    elif args.cmd == "index":
        cmd_index(args.repo)
    elif args.cmd == "context":
        cmd_context(args.repo, " ".join(args.query), args.paths)
    elif args.cmd == "update":
        return cmd_update(args.repo, args.kind, args.title, args.body, args.authority,
                          args.evidence, args.scope, args.accept)
    elif args.cmd == "sync":
        return cmd_sync(args.repo, args.paths, args.apply)
    elif args.cmd == "maintain":
        return cmd_maintain(args.repo, args.fix)
    elif args.cmd == "session-start":
        cmd_session_start()
    elif args.cmd == "turn-inject":
        cmd_turn_inject()
    elif args.cmd == "guard":
        cmd_guard()
    elif args.cmd == "capture":
        cmd_capture(args.event)
    elif args.cmd == "lint":
        for line in cmd_lint(args.repo):
            print(f"- {line}")
    elif args.cmd == "review":
        return cmd_review(args.repo, args.if_due, args.pending, args.approve)
    elif args.cmd == "consolidate":
        return cmd_consolidate(args.repo, args.apply)
    elif args.cmd == "doctor":
        cmd_doctor(args.repo)
    elif args.cmd == "status":
        cmd_status(args.repo)
    elif args.cmd == "search":
        cmd_search(args.repo, args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())
