# Context Forge

<p align="center">
  <img src="assets/context-forge-banner.svg" alt="Context Forge — portable project knowledge for coding agents" width="100%">
</p>

<p align="center">
  <strong>Portable, reviewable project knowledge for coding agents.</strong><br>
  Keep the important context. Load only what the task needs.
</p>

<p align="center">
  <a href="https://github.com/manojvermamv/context-forge"><img src="https://img.shields.io/badge/license-MIT-0E7490?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/manojvermamv/context-forge"><img src="https://img.shields.io/badge/Claude_Code-supported-D97757?style=flat-square" alt="Claude Code supported"></a>
  <a href="https://github.com/manojvermamv/context-forge"><img src="https://img.shields.io/badge/Codex-supported-4F46E5?style=flat-square" alt="Codex supported"></a>
  <a href="https://github.com/manojvermamv/context-forge"><img src="https://img.shields.io/badge/Python-stdlib_only-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python standard library only"></a>
  <a href="https://github.com/manojvermamv/context-forge"><img src="https://img.shields.io/github/stars/manojvermamv/context-forge?style=flat-square&amp;logo=github" alt="GitHub stars"></a>
</p>

Context Forge gives a codebase a small, useful knowledge base that survives across agent sessions. It works with Claude Code and Codex first, and its Markdown files can be read by any agent that follows repository instructions.

It solves two common problems:

- Every new chat starts by rediscovering the project.
- A memory system slowly fills the context window with stale notes.

Context Forge keeps a short hot brief for every session, a larger knowledge store for on-demand lookup, and clear rules for what counts as a fact, a user decision, or an unresolved question.

## Start here

Choose the prompt that matches how much guidance you want. Both preserve your existing repository instructions and agent settings.

### Platform support

Context Forge supports **Windows, macOS, and Linux** with Python 3.9+ and Git.
Use PowerShell on Windows; use a POSIX shell such as Bash or Zsh on macOS and
Linux. The native smoke suite runs on all three systems, while the additional
shell suite runs on macOS and Linux. The included CI workflow verifies these
paths on every push and pull request.

### Prompt 1 — quick setup

Copy this into Claude Code or Codex while you are in the repository you want to set up.

~~~text
Install Context Forge from https://github.com/manojvermamv/context-forge in this repository. Use project scope, preserve all existing instructions and hook settings, initialize and scan the .brain folder, and tell me which installed hooks I need to review (and, for Codex, trust). Do not create requirements or decisions from code alone.
~~~

### Prompt 2 — guided, safe setup

Use this if you want the agent to explain the setup before changing project files.

~~~text
Help me set up Context Forge in this repository step by step. First inspect existing AGENTS.md, CLAUDE.md, Copilot instructions, and hook settings, and preserve them. Install at project scope, initialize and scan .brain, explain the files and generated agent instructions, and show the Codex /hooks trust step. Stop before recording any user decision or requirement.
~~~

### Terminal install

From the root of the project you want Context Forge to remember:

~~~bash
git clone https://github.com/manojvermamv/context-forge.git /tmp/context-forge && python3 /tmp/context-forge/install.py --harness all --scope project --repo "$PWD" && python3 /tmp/context-forge/scripts/brain.py init "$PWD" && python3 /tmp/context-forge/scripts/brain.py scan "$PWD"
~~~

This installs the portable instructions and optional hooks only in the current project, copies the engine to **~/.context-forge**, and creates and scans **.brain/** in the project.

For Windows PowerShell:

~~~powershell
git clone https://github.com/manojvermamv/context-forge.git "$env:TEMP\context-forge"
if ($LASTEXITCODE -eq 0) { python "$env:TEMP\context-forge\install.py" --harness all --scope project --repo (Get-Location); python "$env:TEMP\context-forge\scripts\brain.py" init (Get-Location); python "$env:TEMP\context-forge\scripts\brain.py" scan (Get-Location) }
~~~

After installing for Codex, run **/hooks** once and review each new or changed hook. Codex will not run untrusted hooks.

## How normal work stays friction-free

~~~mermaid
flowchart TD
  A[Agent receives a task] --> B[Read the short route<br/>index, state, status]
  B --> C{Need more project context?}
  C -->|Yes| D[Open only relevant records]
  C -->|No| E[Work and test]
  D --> E
  E --> F{What was learned?}
  F -->|Code-observed fact| G[Update technical record<br/>and traceability]
  F -->|Clear user decision| H[Record accepted decision or requirement<br/>with compact evidence]
  F -->|Unclear or inferred| I[Keep an open question<br/>or private candidate]
  G --> J[Refresh map, index, and registry]
  H --> J
  I --> J
  J --> K[Next agent begins with<br/>small routed context]
~~~

The everyday behavior is simple:

1. **Start small.** The agent reads a short route, current state, and status—not the entire knowledge base.
2. **Open details only when needed.** Decisions, requirements, concepts, the code map, and history remain on disk until the task calls for them.
3. **Keep code facts current.** After verified work, the agent can record code-observed behavior and links to the changed code or tests.
4. **Keep user intent deliberate.** A requirement or decision is durable only after a clear user choice, compact evidence, and explicit acceptance.
5. **Keep uncertainty visible.** If the agent cannot tell whether something is intended, it records a question or a private candidate—not a fact.

This progressive-disclosure approach keeps routine context overhead low. It uses local, deterministic work in hooks instead of a model or MCP call for every lifecycle event.

## What Context Forge remembers

~~~text
.brain/
├── current-state.md       Small hot brief injected at session start
├── index.md               Short map of where to find deeper knowledge
├── map.md                 Generated codebase map
├── overview.md            Project orientation for people and agents
├── status.md              Current goal, blockers, and open questions
├── log.md                 Approved history only
├── decisions/             Durable technical decisions
├── concepts/              On-demand subsystem knowledge
├── requirements/          Explicit user-approved product intent
├── technical/             Code-observed architecture and behavior
├── traceability/          Links from intent and decisions to code/tests
├── questions/             Ambiguous or unresolved intent
├── registry.json           Generated machine-readable catalog
└── .state/                Private candidates, search data, and reports
~~~

**.state/** is private working state. It is ignored by Git and is never treated as project memory.

## What becomes durable knowledge

| Situation | Context Forge records | It does not assume |
|---|---|---|
| A test or code change proves a technical fact | A `technical/` record with `authority: code_observed` | That the implementation reveals product intent |
| You clearly choose a product or design direction | A `decisions/` or `requirements/` record with `authority: user_explicit` and short evidence | That a vague comment, code pattern, or agent guess is approval |
| The intended behavior is uncertain | An open `questions/` record or private candidate | That uncertainty should be promoted to a permanent fact |
| Work happened outside this agent session | A `sync` report; `--apply` can add a code-observed technical record | Raw chat history or a full transcript |

## Safety by design

Self-evolving does **not** mean it writes whatever it wants.

~~~text
hook event → private candidate → your review → named approval → durable memory
~~~

Hooks can collect a concise candidate at session stop, compaction, subagent stop, or session end. They screen for secrets and write only to **.brain/.state/pending/**. Nothing reaches **current-state.md** or **log.md** until you approve a specific candidate.

Direct user-approved decisions are a separate path. While actively working with you, an agent may record an unambiguous choice using `authority: user_explicit`, short sanitized evidence, and `--accept`. This is not hook inference, and it never stores a raw transcript.

~~~bash
# See proposed updates
python3 ~/.context-forge/scripts/brain.py review /path/to/project --pending

# Approve one candidate you have reviewed
python3 ~/.context-forge/scripts/brain.py review /path/to/project --approve <candidate-id>
~~~

The guard hook also blocks direct tool edits to Context Forge’s generated and hot-memory files. It understands Claude Code and Codex apply_patch payloads, including Codex Windows path shapes. Hooks are useful guardrails, not a replacement for normal repository permissions and review.

## Everyday use

Once a project is initialized, continue working normally. Context Forge silently adds relevant pointers when it is confident they help. It stays quiet when it cannot find a useful match.

Use these commands when you want to work with memory directly:

~~~bash
# Refresh the generated code map and routing index
python3 ~/.context-forge/scripts/brain.py map /path/to/project
python3 ~/.context-forge/scripts/brain.py index /path/to/project

# Search deeper memory without loading it all
python3 ~/.context-forge/scripts/brain.py search /path/to/project "authentication"

# Check memory health and see the hot-vs-full-memory reduction
python3 ~/.context-forge/scripts/brain.py doctor /path/to/project

# Check for broken or oversized memory
python3 ~/.context-forge/scripts/brain.py lint /path/to/project
~~~

### Zero-friction knowledge workflow

After `scan`, normal agent work starts from the short `.brain/index.md` router,
then opens only the records relevant to the task. After verified code changes,
the agent refreshes the map and records affected **technical facts** and links.
No separate memory command is needed for that routine work when the agent follows
the installed instructions.

Context Forge never treats code as proof of product intent. An accepted
requirement or ADR needs an explicit user choice, a short sanitized evidence
note, and `authority: user_explicit`. Hooks remain candidate-only because they
cannot reliably prove who said a sentence.

~~~bash
# Create a code-observed baseline for an existing repository
python3 ~/.context-forge/scripts/brain.py scan /path/to/project

# Give any agent a short, task-specific reading list (works without hooks)
python3 ~/.context-forge/scripts/brain.py context /path/to/project "authentication change"

# Record a direct user-approved decision during the active task
python3 ~/.context-forge/scripts/brain.py update /path/to/project \
  --kind decision --title "Use bcrypt for password hashing" \
  --body "Use bcrypt for new password hashes." \
  --authority user_explicit --evidence "User explicitly selected bcrypt." --accept

# Reconcile code changed outside the current agent task
python3 ~/.context-forge/scripts/brain.py sync /path/to/project --apply
python3 ~/.context-forge/scripts/brain.py maintain /path/to/project --fix
~~~

For an important decision or a new technical concept, start with **templates/decision.md** or **templates/concept.md**, then run **index**. Do not put secrets, credentials, raw source dumps, or guesses into the knowledge base.

## Works with your agents

The installer merges its hooks with existing settings; it does not replace unrelated configuration.

| Lifecycle moment | Context Forge action |
|---|---|
| Session starts or resumes after compaction | Load the short hot brief and routes |
| A new user prompt arrives | Offer up to three relevant pointers, or stay silent |
| A tool tries to edit managed memory | Guard the protected paths |
| Stop, compaction, subagent stop, or session end | Stage a proposed update for review |

The same hook lifecycle is wired for Claude Code and Codex. Codex users must approve hook trust through **/hooks**; Claude Code users should review their project or global hook settings as they normally would.

For portable use, project-scope installation adds a marked Context Forge block
to `AGENTS.md`, `.github/copilot-instructions.md`, and `CLAUDE.md` without
overwriting unrelated instructions. These files tell compatible agents how to
read the small route, distinguish intent from technical evidence, and maintain
the knowledge base after work. Agents without hooks can use the same workflow
by running `brain.py context` and reading the Markdown records.

## Install choices

The quick start uses **project scope**, which is best when you want a team to commit the hook configuration with the repository.

~~~bash
# Make Context Forge available to every project for your local user
python3 install.py --harness all --scope global

# Configure only one repository; preserve its existing settings
python3 install.py --harness all --scope project --repo /path/to/project
~~~

Use **--harness claude** or **--harness codex** if you use only one agent. You can also pass **--engine-dir /your/path** when the default **~/.context-forge** location is not suitable.

## Verify the installation

~~~bash
python3 ~/.context-forge/scripts/brain.py status /path/to/project
python3 ~/.context-forge/scripts/brain.py doctor /path/to/project
~~~

Doctor reports the full-memory baseline, the actual cold-start payload, an estimated token count, and the progressive-disclosure reduction. The project’s tests also cover the full staged-review path, compaction reload, persistent memory, hook guard behavior, Codex patch-path handling, and fallback search:

~~~bash
python tests/smoke_test.py
bash tests/smoke_test.sh
~~~

## References

Context Forge combines durable project documentation with the ergonomics of lifecycle hooks:

- **Small by default:** only the hot brief is loaded automatically.
- **Useful when needed:** search routes to a decision, concept, map section, or approved history entry.
- **Safe to share:** session-derived memory is staged and reviewed; direct user-approved decisions retain only compact evidence.
- **Portable:** one Python standard-library engine, no embeddings service, daemon, or database dependency.

Context Forge is informed by the following work:

- [Karpathy’s LLM Wiki note](https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw/ac46de1ad27f92b28ac95459c782c07f6b8c964a/llm-wiki.md) — durable repository knowledge for coding agents.
- [llmwiki](https://github.com/suwonleee/llmwiki) — current thinking on agent-readable project context.
- [Project Wiki](https://github.com/giodra96/project-wiki) — index-first progressive disclosure and the public README presentation that inspired this project’s documentation style.

## Author

Created by [Manoj Verma](https://github.com/manojvermamv).

## License

MIT.
