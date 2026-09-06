---
name: context-forge
description: Portable, reviewable project knowledge in .brain/ for coding agents. Use it whenever a task needs project context, a decision, requirement, technical fact, question, or continuity across sessions. In an enrolled repository, consult .brain/index.md before substantial exploration or a multi-file change, distinguish user intent from code evidence, and use staged review only for hook-captured facts.
---

# context-forge

Use this protocol when the repository contains `.brain/`. It is a portable knowledge base, not a license to write unreviewed agent summaries as fact.

## Read progressively

1. Read `.brain/index.md` first. It is the small routing layer.
2. Read `.brain/current-state.md` and `.brain/status.md` for active decisions, blockers, and next steps.
3. Open only the relevant decision, requirement, technical, question, concept, or map page.
4. Before a broad code search, read `.brain/map.md`; regenerate it with `brain.py map <repo>` if structure has materially changed.
5. For agents without hooks, use `brain.py context <repo> "task words"` to produce the same bounded reading list.

Never load the entire `.brain/` directory just because it exists. The bounded hot tier and on-demand store are the token-saving contract.

## Automatic hook behavior

- `SessionStart` injects only the hot tier and a short route list. Compaction recovery reloads only hot state.
- `UserPromptSubmit` may inject at most three relevant page pointers and is silent when no confident match exists.
- `PreToolUse` protects auto-managed hot memory, generated routing/map files, and private state. It is a guardrail, not a complete file-system security boundary.
- Lifecycle capture (`PreCompact`, `Stop`, `SubagentStop`, `SessionEnd`) creates a bounded, secret-screened **candidate** in `.brain/.state/pending/`. It does not call an LLM, write `log.md`, update `current-state.md`, or promote a fact.

Do not manually recreate lifecycle capture; it is separate from normal knowledge maintenance.

## Required review gate

Inspect candidates before promotion:

```bash
python3 ~/.context-forge/scripts/brain.py review <repo> --pending
python3 ~/.context-forge/scripts/brain.py review <repo> --approve <candidate-id>
```

`--pending` displays the candidate’s exact proposed facts, its ID, and the lifecycle events that produced it. `--approve` promotes exactly that candidate once. Do not approve a candidate that is vague, incorrect, sensitive, or not useful beyond the session. Candidates with redacted secret material are intentionally rejected; write a sanitized human-authored record instead.

This is the only automatic-capture-to-canonical-memory promotion path. Treat the private `.state/` folder as runtime staging, not a source of truth.

## Durable knowledge that needs judgment

Choose the record type from the evidence, not from what would be convenient to remember:

- A clear user choice becomes a `decision` or `requirement` only with `authority: user_explicit`, sanitized evidence, and `--accept`.
- A verified implementation fact becomes `technical` with `authority: code_observed`.
- Uncertain intent becomes a `question`; never upgrade a guess into a requirement.
- A subsystem explanation belongs in an existing `concept` page when it will help future work.
- `traceability/` links accepted intent and decisions to code and tests; do not use it to invent that link.

Use `brain.py update <repo>` for compact, structured records. Use `templates/decision.md`, `templates/concept.md`, and the other knowledge templates when a record needs richer explanation. After manually adding or removing any routed page, run `brain.py index <repo>`; it regenerates the marker-delimited routes and `registry.json`. Never hand-edit that generated route block.

Human/agent-authored overview and concept pages are allowed; generated `map.md`, `index.md`, `current-state.md`, `registry.json`, and `log.md` are not direct-edit targets.

## Portable project knowledge base

Context Forge keeps one plain Markdown/JSON knowledge base for all agents:

- `requirements/` contains only explicit, user-approved product intent.
- `technical/` contains code-observed architecture and behavior.
- `decisions/` contains durable ADRs.
- `traceability/` links records to code and tests.
- `questions/` holds uncertainty instead of invented claims.
- `registry.json` is generated routing metadata for tools; agents should prefer `index.md`.

Run `brain.py scan <repo>` once for an existing repository. It creates only a
code-observed technical baseline. After source changes made outside the current
agent task, run `brain.py sync <repo> --apply`. Run `brain.py maintain <repo>
--fix` for deterministic structure, route, registry, and ID checks.

### User-approved decisions

An active agent may record a durable decision or requirement without a second
chat confirmation only when the user unambiguously chose it in the current
task. The record must use `authority: user_explicit`, include a concise,
sanitized evidence note and its related paths, and be explicitly accepted by
the active workflow. Never use a hook recap, an assistant summary, a code diff,
or an inference as proof of user approval. Code belongs in `technical/` until
explicit intent is available.

## Maintenance commands

```bash
python3 ~/.context-forge/scripts/brain.py map <repo>
python3 ~/.context-forge/scripts/brain.py index <repo>
python3 ~/.context-forge/scripts/brain.py scan <repo>
python3 ~/.context-forge/scripts/brain.py context <repo> "task words"
python3 ~/.context-forge/scripts/brain.py sync <repo> --apply
python3 ~/.context-forge/scripts/brain.py maintain <repo> --fix
python3 ~/.context-forge/scripts/brain.py lint <repo>
python3 ~/.context-forge/scripts/brain.py doctor <repo>
python3 ~/.context-forge/scripts/brain.py search <repo> "query"
python3 ~/.context-forge/scripts/brain.py consolidate <repo>          # review-only plan
python3 ~/.context-forge/scripts/brain.py consolidate <repo> --apply  # explicit canonical rewrite
```

`review <repo> --if-due` is a separate, opt-in semantic review. It needs `BRAIN_LLM_CMD`, is rate-limited, and writes only a private report under `.brain/.state/reviews/`; it does not manufacture canonical facts.

Run `lint` after deliberate wiki changes. Use `doctor` to check real character-budget measurements against the full-wiki baseline; do not make unverified cost claims from a generic benchmark.

## Boundaries

- Never record secrets, credentials, raw private data, or invented decisions.
- Never bypass the staged approval gate for hook-captured facts.
- Never turn an ambiguous task discussion into durable memory without the user’s intent or clear durable value.
- Keep `current-state.md` small; it is injected, while the rest is on demand.
- For Codex, hooks must be reviewed/trusted in `/hooks`; an untrusted hook is expected not to run.
