## context-forge — durable memory in `.brain/`

When `.brain/` exists, use it before re-deriving project context:

1. Read `.brain/index.md`, then only the relevant routed page(s). Read `.brain/map.md` before broad exploration.
2. Treat `current-state.md` as hot, auto-managed memory. Do not hand-edit `current-state.md`, `log.md`, `map.md`, generated routes in `index.md`, or `.brain/.state/`.
3. Hooks stage facts only. To promote a hook-captured fact, inspect then explicitly approve it:
   ```
   python3 ~/.context-forge/scripts/brain.py review <repo> --pending
   python3 ~/.context-forge/scripts/brain.py review <repo> --approve <candidate-id>
   ```
   Reject vague, wrong, or sensitive candidates. A candidate with redactions must not be approved.
4. For a deliberate ADR/concept update, use the corresponding template, merge rather than duplicate topics, then run `brain.py index <repo>`; the generated route block is not manually edited.
5. `consolidate` is plan-only without `--apply`. `review --if-due` is an optional private semantic report, not an automatic canonical write.
6. Never store secrets, raw source dumps, or invented claims. The PreToolUse hook is a guardrail, not a security boundary.

On Codex, newly installed hooks need one-time trust review in `/hooks` before they run.