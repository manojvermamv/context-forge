---
name: context-forge
description: Durable, token-budgeted project memory in .brain/ for Claude Code and Codex. Use it whenever a task needs a past decision, project context, an ADR/concept update, a memory health check, or continuity across sessions. In an enrolled repository, consult .brain/index.md before substantial exploration or a multi-file change, and use the staged review workflow for hook-captured facts.
---

# context-forge

Use this protocol when the repository contains `.brain/`. It is a project memory system, not a license to write unreviewed agent summaries as fact.

## Read progressively

1. Read `.brain/index.md` first. It is the small routing layer.
2. Read `.brain/current-state.md` for active decisions, blockers, and next steps.
3. Open only the decision/concept/map pages relevant to the task.
4. Before a broad code search, read `.brain/map.md`; regenerate it with `brain.py map <repo>` if structure has materially changed.

Never load the entire `.brain/` directory just because it exists. The bounded hot tier and on-demand store are the token-saving contract.

## Automatic hook behavior

- `SessionStart` injects only the hot tier and a short route list. Compaction recovery reloads only hot state.
- `UserPromptSubmit` may inject at most three relevant page pointers and is silent when no confident match exists.
- `PreToolUse` protects auto-managed hot memory, generated routing/map files, and private state. It is a guardrail, not a complete file-system security boundary.
- Lifecycle capture (`PreCompact`, `Stop`, `SubagentStop`, `SessionEnd`) creates a bounded, secret-screened **candidate** in `.brain/.state/pending/`. It does not call an LLM, write `log.md`, update `current-state.md`, or promote a fact.

Do not duplicate hook work manually.

## Required review gate

Inspect candidates before promotion:

```bash
python3 ~/.context-forge/scripts/brain.py review <repo> --pending
python3 ~/.context-forge/scripts/brain.py review <repo> --approve <candidate-id>
```

`--pending` displays the candidate’s exact proposed facts, its ID, and the lifecycle events that produced it. `--approve` promotes exactly that candidate once. Do not approve a candidate that is vague, incorrect, sensitive, or not useful beyond the session. Candidates with redacted secret material are intentionally rejected; write a sanitized human-authored record instead.

This is the only automatic-capture-to-canonical-memory promotion path. Treat the private `.state/` folder as runtime staging, not a source of truth.

## Durable knowledge that needs judgment

When a user explicitly settles a non-obvious decision, or when a finding will genuinely help future work, write an ADR or concept page deliberately:

- Use `templates/decision.md` for `.brain/decisions/ADR-NNN-short-slug.md`. Include context, decision, alternatives considered, and consequences.
- Use `templates/concept.md` for `.brain/concepts/<topic>.md`. Merge into an existing topic rather than creating `-2` duplicates.
- After adding/removing either page, run `brain.py index <repo>`. It regenerates the marker-delimited on-demand routes. Never hand-edit that generated route block.
- Link source locations as `path/to/file:line`; do not paste raw source dumps or secrets.

Human/agent-authored overview and concept pages are allowed; generated `map.md`, `index.md`, `current-state.md`, and `log.md` are not direct-edit targets.

## Maintenance commands

```bash
python3 ~/.context-forge/scripts/brain.py map <repo>
python3 ~/.context-forge/scripts/brain.py index <repo>
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