## context-forge — portable project knowledge in `.brain/`

When `.brain/` exists, use it before re-deriving project context. This protocol is portable: follow it even when this host has no Context Forge hooks.

1. Read `.brain/index.md`, `.brain/current-state.md`, and `.brain/status.md`, then only the relevant routed page(s). Read `.brain/map.md` before broad exploration.
2. Treat `current-state.md` as hot, auto-managed memory. Do not hand-edit `current-state.md`, `log.md`, `map.md`, generated routes in `index.md`, or `.brain/.state/`.
3. Hooks stage facts only. To promote a hook-captured fact, inspect then explicitly approve it:
   ```
   python3 ~/.context-forge/scripts/brain.py review <repo> --pending
   python3 ~/.context-forge/scripts/brain.py review <repo> --approve <candidate-id>
   ```
   Reject vague, wrong, or sensitive candidates. A candidate with redactions must not be approved.
4. Before meaningful code work, read `index.md`, `current-state.md`, `status.md`, then only routed records. `brain.py context <repo> "task words"` provides the same short reading list for any agent.
5. After verified source changes, refresh map/routes and update only affected technical facts and traceability. Code proves observed behavior, not product intent. Use `sync` for changes made outside this task.
6. A direct, unambiguous user choice can be stored as an accepted decision or requirement only with `authority: user_explicit`, concise sanitized evidence, and an explicit acceptance action. Use `brain.py update` when a compact record is enough; use the templates when deeper explanation is needed. A hook recap, an agent inference, or code alone is never proof of user approval.
7. `consolidate` is plan-only without `--apply`. `maintain` is deterministic structural validation; `review --if-due` is an optional private semantic report, not an automatic canonical write.
8. Never store secrets, raw source dumps, or invented claims. The PreToolUse hook is a guardrail, not a security boundary.

On Codex, newly installed hooks need one-time trust review in `/hooks` before they run.
