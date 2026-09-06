#!/usr/bin/env bash
# Portable 13-check smoke test for Context Forge. It uses only a temporary
# repository and runs unchanged on Linux, macOS, and Git Bash (where Python
# needs native Windows paths).
set -euo pipefail

ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORK="$(mktemp -d)"
REPO="$WORK/sample-repo"
trap 'rm -rf "$WORK"' EXIT

native_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

BRAIN_NATIVE="$(native_path "$ENGINE_DIR/scripts/brain.py")"
REPO_NATIVE="$(native_path "$REPO")"
pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; exit 1; }
hook_json() {
  "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(json.loads(sys.stdin.read())))'
}

mkdir -p "$REPO/src/auth"
cd "$REPO" && git init -q
cat > "$REPO/src/auth/login.py" <<'EOF'
class AuthManager:
    def login(self, username, password):
        return True
EOF

run_brain() { "$PYTHON_BIN" "$BRAIN_NATIVE" "$@"; }

# 1–2: deterministic setup.
run_brain init "$REPO_NATIVE" >/dev/null
grep -q "AuthManager" "$REPO/.brain/map.md" && pass "1 map captures expected symbol" || fail "map missing AuthManager"
[ -f "$REPO/.brain/index.md" ] && pass "2 index scaffolded" || fail "index missing"

# 3–5: session load behavior.
OUT=$(printf '%s' '{"hook_event_name":"SessionStart","source":"startup","cwd":"/not-enrolled"}' | run_brain session-start)
[ -z "$OUT" ] && pass "3 unenrolled repo is silent" || fail "unenrolled repo injected context"
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'SessionStart','source':'startup','cwd':r'''$REPO_NATIVE''','session_id':'shell-start'}))" | run_brain session-start)
echo "$OUT" | "$PYTHON_BIN" -c "import json,sys; c=json.load(sys.stdin)['hookSpecificOutput']['additionalContext']; assert 'Current State' in c and '<!-- context-forge:begin -->' in c" \
  && pass "4 hot memory is marker-wrapped" || fail "malformed cold-start injection"
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'SessionStart','source':'future-source','cwd':r'''$REPO_NATIVE''','session_id':'shell-future'}))" | run_brain session-start)
[ -n "$OUT" ] && pass "5 unknown SessionStart source fails open" || fail "future source was rejected"

# 6–7: progressive per-turn routing.
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'UserPromptSubmit','prompt':'what is the capital of France','cwd':r'''$REPO_NATIVE''','session_id':'shell-irrelevant'}))" | run_brain turn-inject)
[ -z "$OUT" ] && pass "6 irrelevant per-turn prompt is silent" || fail "irrelevant prompt injected context"
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'UserPromptSubmit','prompt':'how does AuthManager login work','cwd':r'''$REPO_NATIVE''','session_id':'shell-relevant'}))" | run_brain turn-inject)
echo "$OUT" | grep -q "map.md" && pass "7 relevant prompt returns route pointer" || fail "relevant prompt did not point to map"

# 8–10: direct-write guard, including Codex apply_patch.
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'PreToolUse','tool_name':'Edit','tool_input':{'file_path':r'''$REPO_NATIVE/.brain/map.md'''},'cwd':r'''$REPO_NATIVE'''}))" | run_brain guard)
echo "$OUT" | grep -q '"deny"' && pass "8 generated map direct edit denied" || fail "map edit was allowed"
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'PreToolUse','tool_name':'apply_patch','tool_input':'*** Begin Patch\\n*** Update File: .brain/log.md\\n@@\\n-old\\n+new\\n*** End Patch','cwd':r'''$REPO_NATIVE'''}))" | run_brain guard)
echo "$OUT" | grep -q '"deny"' && pass "9 raw Codex apply_patch protected" || fail "raw apply_patch was allowed"
OUT=$("$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'PreToolUse','tool_name':'Edit','tool_input':{'file_path':r'''$REPO_NATIVE/src/auth/login.py'''},'cwd':r'''$REPO_NATIVE'''}))" | run_brain guard)
[ -z "$OUT" ] && pass "10 normal source edit allowed" || fail "normal source edit denied"

# 11–13: staged capture and one-time promotion.
BEFORE=$(cksum "$REPO/.brain/current-state.md" "$REPO/.brain/log.md")
"$PYTHON_BIN" -c "import json; print(json.dumps({'hook_event_name':'Stop','cwd':r'''$REPO_NATIVE''','session_id':'shell-capture','last_assistant_message':'I decided to use bcrypt instead of plain sha256.'}))" | run_brain capture --event stop
AFTER=$(cksum "$REPO/.brain/current-state.md" "$REPO/.brain/log.md")
[ "$BEFORE" = "$AFTER" ] && pass "11 hook capture stages without canonical write" || fail "capture bypassed approval gate"
PENDING=$(run_brain review "$REPO_NATIVE" --pending)
CANDIDATE_ID=$(printf '%s' "$PENDING" | "$PYTHON_BIN" -c "import json,sys; d=json.load(sys.stdin); print(d['candidates'][0]['id'])")
printf '%s' "$PENDING" | grep -q "bcrypt" && pass "12 pending review exposes proposed facts" || fail "pending candidate not inspectable"
run_brain review "$REPO_NATIVE" --approve "$CANDIDATE_ID" >/dev/null
grep -q "bcrypt" "$REPO/.brain/current-state.md" && grep -q "bcrypt" "$REPO/.brain/log.md" \
  && pass "13 explicit approval promotes exactly one candidate" || fail "approved candidate not promoted"

echo "ALL 13 SHELL SMOKE CHECKS PASSED"
