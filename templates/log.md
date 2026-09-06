# Log

Approved chronological project-memory record. Lifecycle hooks do **not** write here directly: they stage secret-screened candidates in `.brain/.state/pending/`. A developer or agent must inspect a candidate with `brain.py review <repo> --pending` and explicitly promote it with `--approve <candidate-id>` before it appears here.

Entries older than `BRAIN_LOG_ROTATE_DAYS` (default 14) are only moved to `concepts/log-summary-*.md` after reviewing the plan and running `brain.py consolidate <repo> --apply`.

Do not hand-edit this file. Put durable, explanatory knowledge in ADR/concept pages instead.

<!-- approved entries appended below this line -->